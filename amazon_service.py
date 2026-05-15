import os
import json
import logging
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from threading import Lock

try:
    from sp_api.api import CatalogItems, Inventories, Feeds, ListingsItems
    from sp_api.base import Marketplaces
    import time
    import uuid
    import xml.etree.ElementTree as ET
    AMAZON_SP_API_AVAILABLE = True  # Enable real SP-API when available
except ImportError as e:
    AMAZON_SP_API_AVAILABLE = False
    # Create mock classes for when SP-API is not available
    class Marketplaces:
        class US:
            marketplace_id = "A1F83G8C2ARO7P"
    
    class Inventories:
        def __init__(self, **kwargs):
            pass
        
        def get_inventory_summary_marketplace(self, **kwargs):
            class MockResponse:
                def __init__(self):
                    self.payload = {}
            return MockResponse()
    
    class Feeds:
        def __init__(self, **kwargs):
            pass
        
        def create_feed_document(self, **kwargs):
            class MockResponse:
                def __init__(self):
                    self.payload = {'feedDocumentId': 'mock-doc-id', 'url': 'mock-url'}
            return MockResponse()
        
        def create_feed(self, **kwargs):
            class MockResponse:
                def __init__(self):
                    self.payload = {'feedId': 'mock-feed-id'}
            return MockResponse()
        
        def get_feed(self, **kwargs):
            class MockResponse:
                def __init__(self):
                    self.payload = {'processingStatus': 'DONE', 'processingEndTime': '2023-01-01T00:00:00Z'}
            return MockResponse()

from models import InventoryItem, Store, SystemConfig

# Marketplace to Region mapping
MARKETPLACE_REGION = {
    "A1F83G8C2ARO7P": "EU",   # UK
    "A1PA6795UKMFR9": "EU",   # DE
    "A13V1IB3VIYZZH": "EU",   # FR
    "A1RKKUPIHCS9HS": "EU",   # ES
    "APJ6JRA9NG5V4": "EU",    # IT
    "ATVPDKIKX0DER": "NA",    # US
    "A2EUQ1WTGCTBG2": "NA",   # CA
    "A1AM78C64UM0Y8": "NA",   # MX
}

REGION_HOST = {
    "EU": "sellingpartnerapi-eu.amazon.com",
    "NA": "sellingpartnerapi-na.amazon.com",
    "FE": "sellingpartnerapi-fe.amazon.com"
}



def _bt38_price_value(value):
    try:
        if value is None:
            return None
        value = float(value)
        if value <= 0:
            return None
        return value
    except Exception:
        return None


def _bt38_strip_missing_price_fields(payload):
    if not isinstance(payload, dict):
        return payload

    price_keys = [
        "price", "standard_price", "sale_price", "current_price",
        "amount", "value", "listing_price"
    ]

    for key in list(payload.keys()):
        if key.lower() in price_keys and _bt38_price_value(payload.get(key)) is None:
            payload.pop(key, None)

    for key, value in list(payload.items()):
        if isinstance(value, dict):
            _bt38_strip_missing_price_fields(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _bt38_strip_missing_price_fields(item)

    return payload

def resolve_region_host(marketplace_id: str):
    """
    Resolve region and API host from marketplace ID
    
    Args:
        marketplace_id: Amazon marketplace ID (e.g., A1F83G8C2ARO7P)
        
    Returns:
        Tuple of (region, host)
    """
    region = MARKETPLACE_REGION.get(marketplace_id, "NA")
    host = REGION_HOST.get(region, "sellingpartnerapi-eu.amazon.com")
    logging.info(f"Amazon region resolved: marketplace={marketplace_id} → region={region}, host={host}")
    return region, host

# Throttle configuration - prevent QuotaExceeded errors
AMAZON_MIN_INTERVAL_SECONDS = int(os.getenv("AMAZON_MIN_INTERVAL_SECONDS", "75"))
AMAZON_RETRY_BACKOFF = [15, 30, 60, 120, 300]
AMAZON_BATCH_MAX_SKUS = int(os.getenv("AMAZON_BATCH_MAX_SKUS", "100"))
AMAZON_JITTER_MAX = int(os.getenv("AMAZON_JITTER_MAX", "7"))

# Module-level timestamp tracking for feed submission throttling
_last_feed_ts = {}  # Dict[region_key, float] - timestamp of last successful feed creation
_feed_ts_lock = Lock()

# Regional feed creation locks to prevent parallel feed submission
FEED_LOCKS = {
    "eu": Lock(),
    "na": Lock(),
    "fe": Lock(),
}

class QuotaExceededError(Exception):
    """Raised when Amazon SP-API returns QuotaExceeded error"""
    pass

def _sleep_with_jitter(base: float, attempt: int, cap: float = 120.0):
    """
    Exponential backoff with full jitter
    
    Args:
        base: Base delay in seconds
        attempt: Attempt number (0-indexed)
        cap: Maximum delay cap in seconds
    """
    delay = min(cap, base * (2 ** attempt))
    actual_delay = random.uniform(0, delay)
    time.sleep(actual_delay)
    logging.debug(f"Throttle backoff: sleeping {actual_delay:.2f}s (attempt {attempt + 1})")

def _throttle_feed_submission(marketplace_id: str):
    """
    Enforce minimum interval between feed submissions for a region
    Sleeps if needed to maintain AMAZON_MIN_INTERVAL_SECONDS spacing
    
    Args:
        marketplace_id: Amazon marketplace ID to determine region
    """
    region = region_from_marketplace(marketplace_id)
    now = time.time()
    
    with _feed_ts_lock:
        last_ts = _last_feed_ts.get(region, 0)
        elapsed = now - last_ts
        
        if elapsed < AMAZON_MIN_INTERVAL_SECONDS:
            wait_time = AMAZON_MIN_INTERVAL_SECONDS - elapsed
            jitter = random.uniform(0, AMAZON_JITTER_MAX)
            total_wait = wait_time + jitter
            logging.info(f"Throttle guard: waiting {total_wait:.1f}s before feed submission (region={region}, elapsed={elapsed:.1f}s)")
            time.sleep(total_wait)

def _update_feed_timestamp(marketplace_id: str):
    """Update the last successful feed timestamp for a region"""
    region = region_from_marketplace(marketplace_id)
    with _feed_ts_lock:
        _last_feed_ts[region] = time.time()
        logging.debug(f"Updated feed timestamp for region={region}")

def region_from_marketplace(marketplace_id: str) -> str:
    """
    Map marketplace ID to region for feed serialization
    
    Args:
        marketplace_id: Amazon marketplace ID
        
    Returns:
        Region key: 'eu', 'na', or 'fe'
    """
    EU_MARKETPLACES = {
        "A1F83G8C2ARO7P",  # UK
        "A13V1IB3VIYZZH",  # DE
        "A1RKKUPIHCS9HS",  # ES
        "APJ6JRA9NG5V4",   # IT
        "A1805IZSGTT6HS",  # NL
        "A2NODRKZP88ZB9",  # SE
        "AMEN7PMS3EDWL",   # BE
        "A1PA6795UKMFR9",  # FR
        "A17E79C6D8DWNP",  # SA
        "A2Q3Y263D00KWC",  # BR (sometimes grouped with NA)
    }
    
    NA_MARKETPLACES = {
        "ATVPDKIKX0DER",   # US
        "A2EUQ1WTGCTBG2",  # CA
        "A1AM78C64UM0Y8",  # MX
    }
    
    if marketplace_id in EU_MARKETPLACES:
        return "eu"
    elif marketplace_id in NA_MARKETPLACES:
        return "na"
    else:
        return "fe"  # Far East (JP, AU, SG, etc.)

class AmazonAPIService:
    """Amazon SP-API integration service for inventory management"""
    
    def __init__(self, marketplace_region='US'):
        # Support different marketplaces
        # Use proper marketplace configuration
        if AMAZON_SP_API_AVAILABLE:
            marketplace_map = {
                'US': Marketplaces.US,
                'UK': Marketplaces.UK, 
                'DE': Marketplaces.DE,
                'FR': Marketplaces.FR,
                'IT': Marketplaces.IT,
                'ES': Marketplaces.ES,
                'CA': Marketplaces.CA,
            }
        else:
            marketplace_map = {}
            
        # For regions, also support the marketplace ID directly
        marketplace_id_map = {
            'US': 'ATVPDKIKX0DER',
            'UK': 'A1F83G8C2ARO7P',
            'DE': 'A1PA6795UKMFR9',
            'FR': 'A13V1IB3VIYZZH',
            'IT': 'APJ6JRA9NG5V4',
            'ES': 'A1RKKUPIHCS9HS',
            'CA': 'A2EUQ1WTGCTBG2'
        }
        
        if AMAZON_SP_API_AVAILABLE:
            # Accept country code, region code, or direct Amazon marketplace ID.
            marketplace_id_to_code = {
                'A1F83G8C2ARO7P': 'UK',
                'A1PA6795UKMFR9': 'DE',
                'A13V1IB3VIYZZH': 'FR',
                'A1RKKUPIHCS9HS': 'ES',
                'APJ6JRA9NG5V4': 'IT',
                'ATVPDKIKX0DER': 'US',
                'A2EUQ1WTGCTBG2': 'CA',
            }

            region_to_default_code = {
                'EU': 'UK',
                'NA': 'US',
                'FE': 'US',
            }

            resolved_code = marketplace_id_to_code.get(
                marketplace_region,
                region_to_default_code.get(marketplace_region, marketplace_region)
            )

            self.marketplace = marketplace_map.get(resolved_code, marketplace_map.get('UK'))
        else:
            # Mock mode - create mock marketplace
            class MockMarketplace:
                marketplace_id = "A1F83G8C2ARO7P"  # UK default
            self.marketplace = MockMarketplace()
            
        # Final fallback in case marketplace is still None
        if not self.marketplace:
            class MockMarketplace:
                marketplace_id = "A1F83G8C2ARO7P"
            self.marketplace = MockMarketplace()
            
        self.logger = logging.getLogger(__name__)
        
    def _generate_inventory_feed_xml(self, item: InventoryItem) -> str:
        """
        Generate XML feed for inventory quantity update
        Returns XML string for POST_INVENTORY_AVAILABILITY_DATA feed
        """
        # Create the root XML structure for inventory feed
        root = ET.Element('AmazonEnvelope')
        root.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
        root.set('xsi:noNamespaceSchemaLocation', 'amzn-envelope.xsd')
        
        # Header
        header = ET.SubElement(root, 'Header')
        document_version = ET.SubElement(header, 'DocumentVersion')
        document_version.text = '1.01'
        merchant_identifier = ET.SubElement(header, 'MerchantIdentifier')
        merchant_identifier.text = 'MERCHANT_ID_PLACEHOLDER'  # Will be replaced with actual merchant ID
        
        # Message type
        message_type = ET.SubElement(root, 'MessageType')
        message_type.text = 'Inventory'
        
        # Message
        message = ET.SubElement(root, 'Message')
        message_id = ET.SubElement(message, 'MessageID')
        message_id.text = '1'
        
        # Inventory element
        inventory = ET.SubElement(message, 'Inventory')
        
        # SKU
        sku_element = ET.SubElement(inventory, 'SKU')
        sku_element.text = item.sku
        
        # Quantity
        quantity_element = ET.SubElement(inventory, 'Quantity')
        quantity_element.text = str(max(0, item.quantity))  # Ensure non-negative
        
        # Convert to string
        xml_str = ET.tostring(root, encoding='unicode')
        # Add XML declaration
        return f'<?xml version="1.0" encoding="utf-8"?>\n{xml_str}'
    
    def _create_feed_document(self, feeds_client, content: str) -> Tuple[bool, str, Optional[str]]:
        """
        Create feed document and upload encrypted content
        Returns (success, message, feed_document_id)
        """
        try:
            # Call create_feed_document with file=None to prevent automatic upload
            # This allows us to handle encryption ourselves
            create_doc_response = feeds_client.create_feed_document(
                file=None,  # Skip automatic upload!
                content_type='text/xml; charset=utf-8'
            )
            
            if not create_doc_response.payload:
                return False, "Failed to create feed document", None
                
            feed_document_id = create_doc_response.payload.get('feedDocumentId')
            upload_url = create_doc_response.payload.get('url')
            encryption_details = create_doc_response.payload.get('encryptionDetails', {})
            
            self.logger.info(f"Feed document created: {feed_document_id}")
            self.logger.info(f"Encryption required: {bool(encryption_details)}")
            
            if not feed_document_id or not upload_url:
                return False, "Invalid feed document response", None
            
            # Prepare content for upload
            upload_data = content.encode('utf-8')
            upload_headers = {'Content-Type': 'text/xml; charset=utf-8'}
            
            # Amazon ALWAYS requires encryption - if encryptionDetails present, use them
            if encryption_details:
                try:
                    from Crypto.Cipher import AES
                    from Crypto.Util.Padding import pad
                    import base64
                    
                    # Extract encryption parameters from Amazon
                    key = base64.b64decode(encryption_details.get('key'))
                    iv = base64.b64decode(encryption_details.get('initializationVector'))
                    
                    # Encrypt content using AES-256-CBC
                    cipher = AES.new(key, AES.MODE_CBC, iv)
                    encrypted_data = cipher.encrypt(pad(upload_data, AES.block_size))
                    upload_data = encrypted_data
                    
                    self.logger.info(f"✅ Encrypted feed content using AES-256-CBC (key:{len(key)} bytes, iv:{len(iv)} bytes)")
                    
                except ImportError:
                    self.logger.error("❌ pycryptodome not installed - cannot encrypt feed")
                    return False, "Feed encryption required but pycryptodome not available", None
                except Exception as enc_error:
                    self.logger.error(f"❌ Encryption error: {str(enc_error)}")
                    return False, f"Feed encryption failed: {str(enc_error)}", None
            else:
                self.logger.warning("⚠️  No encryption details in response - uploading unencrypted (may fail)")
            
            # Upload content to the presigned URL
            import requests
            self.logger.info(f"Uploading feed content ({len(upload_data)} bytes)...")
            upload_response = requests.put(
                upload_url,
                data=upload_data,
                headers=upload_headers
            )
            
            if upload_response.status_code not in [200, 201]:
                return False, f"Failed to upload feed content: HTTP {upload_response.status_code}", None
                
            self.logger.info(f"✅ Feed upload successful (HTTP {upload_response.status_code})")
            return True, "Feed document created successfully", feed_document_id
            
        except Exception as e:
            self.logger.error(f"Error creating feed document: {str(e)}")
            return False, f"Feed document creation error: {str(e)}", None
    
    def _download_feed_result(self, feeds_client, result_document_id: str) -> Optional[str]:
        """
        Download and parse feed result document to extract errors
        Returns error details as a string, or None if unable to download
        """
        try:
            import requests
            
            # Get the result document metadata
            doc_response = feeds_client.get_feed_document(feedDocumentId=result_document_id)
            if not doc_response.payload:
                return None
            
            download_url = doc_response.payload.get('url')
            if not download_url:
                return None
            
            # Download the result document
            result_response = requests.get(download_url)
            if result_response.status_code != 200:
                return f"Failed to download result document: HTTP {result_response.status_code}"
            
            result_content = result_response.text
            
            # Parse the result to extract errors
            # Amazon feed results can be JSON or XML
            try:
                # Try JSON first
                result_data = json.loads(result_content)
                errors = []
                
                # Extract error messages from JSON structure
                if isinstance(result_data, dict):
                    if 'errors' in result_data:
                        for error in result_data.get('errors', []):
                            errors.append(f"{error.get('code', 'ERROR')}: {error.get('message', 'Unknown error')}")
                    if 'messages' in result_data:
                        for msg in result_data.get('messages', []):
                            if msg.get('resultCode') != 'Success':
                                errors.append(f"{msg.get('resultCode', 'ERROR')}: {msg.get('resultDescription', 'Unknown error')}")
                
                return '; '.join(errors) if errors else result_content[:500]
                
            except json.JSONDecodeError:
                # Try XML parsing
                try:
                    root = ET.fromstring(result_content)
                    errors = []
                    
                    # Look for error elements in various structures
                    for error_elem in root.findall('.//Error'):
                        code = error_elem.findtext('Code', 'ERROR')
                        message = error_elem.findtext('Message', 'Unknown error')
                        errors.append(f"{code}: {message}")
                    
                    for result_elem in root.findall('.//Result'):
                        result_code = result_elem.findtext('ResultCode', '')
                        if result_code and result_code != 'Success':
                            desc = result_elem.findtext('ResultDescription', 'Unknown error')
                            errors.append(f"{result_code}: {desc}")
                    
                    return '; '.join(errors) if errors else result_content[:500]
                    
                except ET.ParseError:
                    # Return raw content if we can't parse
                    return result_content[:500]
                    
        except Exception as e:
            self.logger.error(f"Error downloading feed result: {str(e)}")
            return f"Error downloading result: {str(e)}"
    
    def _create_feed_with_backoff(
        self,
        feeds_client,
        feed_payload: Dict[str, Any],
        max_attempts: int = 6,
        base_delay: float = 3.0
    ) -> Dict[str, Any]:
        """
        Create feed with exponential backoff on QuotaExceeded errors
        
        Args:
            feeds_client: Feeds API client
            feed_payload: Payload for create_feed (feedType, marketplaceIds, inputFeedDocumentId)
            max_attempts: Maximum retry attempts
            base_delay: Base delay for exponential backoff
            
        Returns:
            Feed response payload
            
        Raises:
            QuotaExceededError: If quota exceeded after all retries
            Exception: For other API errors
        """
        attempt = 0
        while True:
            try:
                self.logger.info(f"Creating feed (attempt {attempt + 1}/{max_attempts})...")
                # BT38: remove missing price fields before Amazon call; stock-only push is allowed

                if "payload" in locals():

                    payload = _bt38_strip_missing_price_fields(payload)

                response = feeds_client.create_feed(**feed_payload)
                
                # Check for errors in response
                if hasattr(response, 'errors') and response.errors:
                    errors = response.errors
                    if any(e.get('code') == 'QuotaExceeded' for e in errors):
                        raise QuotaExceededError(f"Amazon API quota exceeded: {errors}")
                    # Other errors - don't retry
                    raise Exception(f"Feed creation failed: {errors}")
                
                # Success!
                if response.payload:
                    self.logger.info(f"✅ Feed created successfully: {response.payload.get('feedId')}")
                    return response.payload
                else:
                    raise Exception("No payload in feed response")
                    
            except QuotaExceededError as qe:
                if attempt >= max_attempts - 1:
                    self.logger.error(f"❌ QuotaExceeded after {max_attempts} attempts: {str(qe)}")
                    raise
                
                self.logger.warning(f"⚠️  QuotaExceeded on attempt {attempt + 1}, retrying with backoff...")
                _sleep_with_jitter(base_delay, attempt)
                attempt += 1
                
            except Exception as e:
                # Non-quota errors don't get retried
                self.logger.error(f"❌ Feed creation error (non-quota): {str(e)}")
                raise
    
    def _generate_batched_inventory_feed_xml(
        self,
        items: List[Tuple[str, int]],
        seller_id: str
    ) -> str:
        """
        Generate XML feed for multiple inventory updates in one feed
        
        Args:
            items: List of (sku, quantity) tuples
            seller_id: Amazon seller/merchant ID
            
        Returns:
            XML string for POST_INVENTORY_AVAILABILITY_DATA feed
        """
        root = ET.Element('AmazonEnvelope')
        root.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
        root.set('xsi:noNamespaceSchemaLocation', 'amzn-envelope.xsd')
        
        # Header
        header = ET.SubElement(root, 'Header')
        document_version = ET.SubElement(header, 'DocumentVersion')
        document_version.text = '1.01'
        merchant_identifier = ET.SubElement(header, 'MerchantIdentifier')
        merchant_identifier.text = seller_id
        
        # Message type
        message_type = ET.SubElement(root, 'MessageType')
        message_type.text = 'Inventory'
        
        # Add a message for each SKU
        for idx, (sku, quantity) in enumerate(items, start=1):
            message = ET.SubElement(root, 'Message')
            message_id = ET.SubElement(message, 'MessageID')
            message_id.text = str(idx)
            
            inventory = ET.SubElement(message, 'Inventory')
            
            sku_element = ET.SubElement(inventory, 'SKU')
            sku_element.text = sku
            
            quantity_element = ET.SubElement(inventory, 'Quantity')
            quantity_element.text = str(max(0, quantity))
        
        xml_str = ET.tostring(root, encoding='unicode')
        return f'<?xml version="1.0" encoding="utf-8"?>\n{xml_str}'
    
    def update_listing_quantity_patch(
        self,
        store: Store,
        sku: str,
        quantity: int,
        marketplace_id: str
    ) -> Tuple[bool, str]:
        """
        Update quantity via Listings Items PATCH API (bypasses Feeds quota)
        
        This method uses a different API quota than Feeds, making it useful for:
        - Urgent MFN quantity updates when Feeds are throttled
        - Small batch updates (1-50 SKUs)
        - Critical stock corrections
        
        IMPORTANT: Only works for MFN (Merchant Fulfilled Network) offers.
        AFN/FBA quantities are controlled by Amazon and will be ignored.
        
        Args:
            store: Store object with Amazon credentials
            sku: Seller SKU to update
            quantity: New quantity (will be clamped to >= 0)
            marketplace_id: Amazon marketplace ID (e.g., "A1F83G8C2ARO7P" for UK)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            if not AMAZON_SP_API_AVAILABLE:
                return False, "Amazon SP-API library not installed"
            
            # Skip AFN/FBA SKUs (Amazon controls their quantities)
            if sku and ('-FBA' in sku.upper() or '-AFN' in sku.upper()):
                return False, f"SKU {sku} appears to be AFN/FBA - quantity controlled by Amazon"
            
            # Parse credentials
            if not store.api_key:
                return False, "No API credentials configured for store"
            
            creds = json.loads(store.api_key)
            
            # Extract required credentials
            credentials_dict = {
                'refresh_token': self._clean_credential(creds.get('refresh_token', '')),
                'lwa_app_id': self._clean_credential(creds.get('lwa_app_id', '') or creds.get('client_id', '')),
                'lwa_client_secret': self._clean_credential(creds.get('lwa_client_secret', '') or creds.get('client_secret', '')),
            }
            
            # Optional: AWS credentials if using role assumption
            if creds.get('aws_access_key'):
                credentials_dict['aws_access_key'] = self._clean_credential(creds.get('aws_access_key'))
                credentials_dict['aws_secret_key'] = self._clean_credential(creds.get('aws_secret_key'))
            
            if creds.get('role_arn'):
                credentials_dict['role_arn'] = creds.get('role_arn')
            
            seller_id = creds.get('seller_id', '')
            
            # Validate required credentials
            if not credentials_dict['refresh_token'] or not credentials_dict['lwa_app_id']:
                return False, "Missing required credentials (refresh_token, lwa_app_id/lwa_client_id)"
            
            # Map marketplace ID to Marketplaces object
            marketplace_map = {
                'A1F83G8C2ARO7P': Marketplaces.UK,
                'A13V1IB3VIYZZH': Marketplaces.DE,
                'A1RKKUPIHCS9HS': Marketplaces.ES,
                'APJ6JRA9NG5V4': Marketplaces.IT,
                'A1PA6795UKMFR9': Marketplaces.FR,
                'A2EUQ1WTGCTBG2': Marketplaces.CA,
            }
            
            marketplace = marketplace_map.get(marketplace_id)
            if not marketplace:
                return False, f"Unsupported marketplace ID: {marketplace_id}"
            
            # Initialize Listings Items API client
            self.logger.info(f"Updating {sku} quantity to {quantity} via Listings PATCH (marketplace: {marketplace_id})")
            
            listings_client = ListingsItems(
                credentials=credentials_dict,
                marketplace=marketplace
            )
            
            # Build PATCH request body
            patch_body = {
                "productType": "PRODUCT",
                "patches": [{
                    "op": "replace",
                    "path": "/attributes/fulfillment_availability",
                    "value": [{
                        "fulfillment_channel_code": "DEFAULT",  # MFN
                        "quantity": max(0, int(quantity))  # Ensure non-negative
                    }]
                }]
            }
            
            # Execute PATCH request
            # BT38: remove missing price fields before Amazon call; stock-only push is allowed

            if "payload" in locals():

                payload = _bt38_strip_missing_price_fields(payload)

            response = listings_client.patch_listings_item(
                sellerId=seller_id,
                sku=sku,
                marketplaceIds=[marketplace_id],
                body=patch_body
            )
            
            # Check response
            if hasattr(response, 'errors') and response.errors:
                error_messages = [f"{e.get('code', 'ERROR')}: {e.get('message', 'Unknown')}" for e in response.errors]
                error_str = '; '.join(error_messages)
                self.logger.error(f"❌ Listings PATCH failed for {sku}: {error_str}")
                return False, f"Listings API error: {error_str}"
            
            if response.payload:
                self.logger.info(f"✅ Successfully updated {sku} quantity to {quantity} via Listings PATCH")
                return True, f"Successfully updated {sku} to quantity {quantity} (Listings API)"
            else:
                return True, f"Update submitted for {sku} (no response payload)"
                
        except Exception as e:
            self.logger.error(f"Error updating {sku} via Listings PATCH: {str(e)}")
            return False, f"Listings PATCH error: {str(e)}"
    
    def _poll_feed_status(self, feeds_client, feed_id: str, max_attempts: int = 30, delay_seconds: int = 10) -> Tuple[bool, str]:
        """
        Poll feed processing status until completion or timeout
        Returns (success, message)
        """
        try:
            for attempt in range(max_attempts):
                try:
                    feed_response = feeds_client.get_feed(feedId=feed_id)
                    
                    if not feed_response.payload:
                        continue
                        
                    status = feed_response.payload.get('processingStatus')
                    result_document_id = feed_response.payload.get('resultFeedDocumentId')
                    
                    if status == 'DONE':
                        processing_end_time = feed_response.payload.get('processingEndTime')
                        # Check for errors even in DONE status
                        if result_document_id:
                            error_details = self._download_feed_result(feeds_client, result_document_id)
                            if error_details and ('error' in error_details.lower() or 'failed' in error_details.lower()):
                                return False, f"Feed completed with errors: {error_details}"
                        return True, f"Feed processing completed successfully at {processing_end_time}"
                    elif status == 'FATAL':
                        # Download result document to get detailed error
                        detailed_error = "Feed processing failed with fatal error"
                        if result_document_id:
                            error_details = self._download_feed_result(feeds_client, result_document_id)
                            if error_details:
                                detailed_error = f"Feed failed: {error_details}"
                        return False, detailed_error
                    elif status == 'CANCELLED':
                        return False, "Feed processing was cancelled"
                    elif status in ['IN_QUEUE', 'IN_PROGRESS']:
                        self.logger.info(f"Feed {feed_id} status: {status}, waiting...")
                        if attempt < max_attempts - 1:  # Don't sleep on last attempt
                            time.sleep(delay_seconds)
                        continue
                    else:
                        self.logger.warning(f"Unknown feed status: {status}")
                        if attempt < max_attempts - 1:
                            time.sleep(delay_seconds)
                            
                except Exception as poll_error:
                    self.logger.warning(f"Error polling feed status (attempt {attempt + 1}): {str(poll_error)}")
                    if attempt < max_attempts - 1:
                        time.sleep(delay_seconds)
                        
            return False, f"Feed processing timeout after {max_attempts} attempts"
            
        except Exception as e:
            return False, f"Error polling feed status: {str(e)}"
        
    def _clean_credential(self, value: str) -> str:
        """Remove Replit 'Value:' prefix from credentials if present"""
        if not value:
            return value
        if 'Value:' in value:
            return value.split('Value:')[-1].strip()
        return value.strip()
    
    def _get_db_config(self, key):
        """Get config value from database"""
        try:
            config = SystemConfig.query.filter_by(key=key).first()
            return config.value if config else None
        except:
            return None
    
    def authenticate_store(self, store: Store) -> bool:
        """
        Authenticate Amazon store using SP-API credentials
        Returns True if authentication successful, False otherwise
        """
        try:
            # Priority: 1) Store credentials, 2) Database credentials, 3) Environment variables
            # Check store credentials FIRST (highest priority)
            if store.api_key:
                self.logger.info(f"Using store-specific credentials for authentication: {store.name}")
                creds = json.loads(store.api_key)
                required_keys = ['refresh_token', 'lwa_app_id', 'lwa_client_secret']
                
                if all(key in creds for key in required_keys):
                    self.logger.debug(f"Client ID from store: {creds['lwa_app_id'][:50]}...")
                else:
                    self.logger.error(f"Missing required credentials in store {store.name}")
                    return False
            else:
                # Check database credentials second
                db_refresh_token = self._get_db_config('AMAZON_REFRESH_TOKEN')
                db_client_id = self._get_db_config('AMAZON_LWA_CLIENT_ID')
                db_client_secret = self._get_db_config('AMAZON_LWA_CLIENT_SECRET')
                db_seller_id = self._get_db_config('AMAZON_SELLER_ID')
                
                if db_refresh_token and db_client_id and db_client_secret and db_seller_id:
                    self.logger.info(f"Using database credentials for authentication: {store.name}")
                    self.logger.debug(f"Client ID from DB: {db_client_id[:50]}...")
                    creds = {
                        'refresh_token': db_refresh_token,
                        'lwa_app_id': db_client_id,
                        'lwa_client_secret': db_client_secret,
                        'seller_id': db_seller_id
                    }
                else:
                    # Fall back to environment credentials (lowest priority)
                    env_refresh_token = self._clean_credential(os.environ.get('AMAZON_REFRESH_TOKEN'))
                    env_client_id = self._clean_credential(None)
                    env_client_secret = self._clean_credential(os.environ.get('AMAZON_LWA_CLIENT_SECRET'))
                    env_seller_id = self._clean_credential(None)
                    
                    if env_refresh_token and env_client_id and env_client_secret and env_seller_id:
                        self.logger.info(f"Using environment credentials for authentication: {store.name}")
                        self.logger.debug(f"Client ID from env: {env_client_id[:50]}...")
                        creds = {
                            'refresh_token': env_refresh_token,
                            'lwa_app_id': env_client_id,
                            'lwa_client_secret': env_client_secret,
                            'seller_id': env_seller_id
                        }
                    else:
                        self.logger.error(f"No API credentials found for store {store.name}")
                        return False
                
            # Check if SP-API is available
            if not AMAZON_SP_API_AVAILABLE:
                self.logger.info(f"Amazon SP-API in mock mode - simulating authentication for store: {store.name}")
                return True  # Allow mock mode testing
                
            # Test connection with a simple API call using credentials dict
            credentials = {
                'refresh_token': creds['refresh_token'],
                'lwa_app_id': creds['lwa_app_id'], 
                'lwa_client_secret': creds['lwa_client_secret']
            }
            inventory_client = Inventories(
                marketplace=self.marketplace,
                credentials=credentials
            )
            
            # Make a test call to verify credentials
            test_response = inventory_client.get_inventory_summary_marketplace(
                granularityType='Marketplace',
                granularityId=self.marketplace.marketplace_id,
                marketplaceIds=[self.marketplace.marketplace_id]
            )
            
            self.logger.info(f"Successfully authenticated Amazon store: {store.name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Authentication failed for store {store.name}: {str(e)}")
            return False
    
    def import_inventory_from_amazon(self, store: Store) -> Tuple[bool, List[Dict], str]:
        """
        Import all inventory items from Amazon store
        Returns (success, items_list, message)
        """
        try:
            # Priority: Store credentials first, then environment
            if store.api_key:
                # Use store-specific credentials (highest priority)
                self.logger.info(f"Using store-specific credentials for Amazon import: {store.name}")
                creds = json.loads(store.api_key)
                required_keys = ['refresh_token', 'lwa_app_id', 'lwa_client_secret']
                
                if not all(key in creds for key in required_keys):
                    return False, [], "Missing required credentials"
            else:
                return False, [], "No API credentials found"
            
            # Force attempt to use real SP-API - removed flag check
            # The SP-API library is installed and available, so let's try real connections
            self.logger.info(f"Amazon SP-API attempting real connection for store: {store.name}")
            
            # Only use mock mode if we absolutely can't import the modules
            if not AMAZON_SP_API_AVAILABLE:
                self.logger.info(f"Amazon SP-API in mock mode - simulating inventory import for store: {store.name}")
                mock_items = [
                    {
                        'quantity': 25
                    },
                    {
                        'quantity': 50
                    }
                ]
                return True, mock_items, f"Successfully imported {len(mock_items)} mock items from Amazon (test mode)"
                
            # Get seller ID from credentials
            seller_id = creds.get('seller_id')
            if not seller_id:
                return False, [], "No seller_id found in credentials"
                
            # Initialize listings client with credentials dict
            credentials = {
                'refresh_token': creds['refresh_token'],
                'lwa_app_id': creds['lwa_app_id'],
                'lwa_client_secret': creds['lwa_client_secret']
            }
            listings_client = ListingsItems(
                marketplace=self.marketplace,
                credentials=credentials
            )
            
            imported_items = []
            
            # Fetch ALL active listings from Amazon with pagination (includes FBA and FBM)
            self.logger.info(f"Fetching ALL active listings from Amazon for seller: {seller_id}")
            
            # Pagination loop to get ALL listings items
            page_token = None
            page_count = 0
            
            while True:
                page_count += 1
                self.logger.info(f"Fetching Amazon listings page {page_count}...")
                
                try:
                    # Build API call params for listings search
                    api_params = {
                        'sellerId': seller_id,
                        'marketplaceIds': [self.marketplace.marketplace_id],
                        'includedData': ['summaries', 'fulfillmentAvailability'],  # Get fulfillment details
                        'pageSize': 20  # Maximum page size for Listings API
                    }
                    
                    # Add pageToken for pagination if present
                    if page_token:
                        api_params['pageToken'] = page_token
                    
                    # Call Amazon SP-API to get ALL listings
                    response = listings_client.search_listings_items(**api_params)
                    
                except Exception as api_error:
                    self.logger.error(f"Amazon SP-API call failed on page {page_count}: {str(api_error)}")
                    if page_count == 1:
                        return False, [], f"Failed to connect to Amazon SP-API: {str(api_error)}"
                    else:
                        # If we got at least one page, return what we have
                        break
                
                if hasattr(response, 'payload') and response.payload:
                    
                    listings_items = response.payload.get('items', [])
                    self.logger.info(f"Processing {len(listings_items)} items from page {page_count}")
                    
                    for item in listings_items:
                        try:
                            
                            # Extract seller SKU - REQUIRED field from Listings API
                            sku = item.get('sku', '')
                            if not sku:
                                self.logger.warning(f"Skipping item with no SKU: {item}")
                                continue
                            
                            # Check listing status from summaries - must be BUYABLE to import
                            summaries = item.get('summaries', [])
                            if not summaries or len(summaries) == 0:
                                self.logger.warning(f"Skipping item {sku} with no summaries")
                                continue
                            
                            # Extract data from first summary
                            summary = summaries[0]
                            
                            # Get ASIN from summary - REQUIRED
                            asin = summary.get('asin', '')
                            if not asin:
                                self.logger.warning(f"Skipping item {sku} with no ASIN in summary")
                                continue
                            
                            # Get status and product details
                            status_list = summary.get('status', [])
                            is_buyable = 'BUYABLE' in status_list
                            
                            # Get condition and product name
                            condition = summary.get('conditionType', 'NewItem')
                            product_name = summary.get('itemName', sku)  # Default to SKU if no name
                            fn_sku = summary.get('fnSku', '')  # FNSKU if available
                            
                            # IMPORT ALL LISTINGS regardless of buyable status or quantity
                            # This allows for product grouping even for out-of-stock or inactive items
                            self.logger.debug(f"Importing listing {sku}: buyable={is_buyable}, status={status_list}")
                            
                            # Parse FBA/FBM quantities from fulfillmentAvailability
                            quantity = 0
                            fulfillment_type = "FBM"  # Default
                            
                            fulfillment_data = item.get('fulfillmentAvailability', [])
                            for fulfillment in fulfillment_data:
                                # Get fulfillment channel code
                                channel = fulfillment.get('fulfillmentChannelCode', 'DEFAULT')
                                
                                if channel == 'DEFAULT':
                                    # FBM (Merchant-Fulfilled)
                                    fulfillment_type = "FBM"
                                    quantity = fulfillment.get('quantity', 0)
                                    self.logger.debug(f"FBM item {sku}: {quantity} units")
                                elif 'AMAZON' in channel:
                                    # FBA (Amazon-Fulfilled)
                                    fulfillment_type = "FBA"
                                    quantity = fulfillment.get('quantity', 0)
                                    # Use fn_sku from summary if available, otherwise use ASIN
                                    if not fn_sku:
                                        fn_sku = asin
                                    self.logger.debug(f"FBA item {sku}: {quantity} units (channel: {channel})")
                            
                            # Log quantity calculation for debugging
                            self.logger.debug(f"Amazon listing {sku}: quantity={quantity}, type={fulfillment_type}, ASIN={asin}")
                            
                            # For Listings API, we don't have detailed inbound quantities
                            # So we'll import all BUYABLE listings regardless of quantity
                            # The user can manage 0-stock items through the interface
                            
                            # Initialize product details with defaults
                            # product_name already set from summaries above
                            product_description = ''
                            product_brand = ''
                            product_manufacturer = ''
                            product_price = 0.0
                            product_images = []
                            product_category = ''
                            product_dimensions = {}
                            product_weight = ''
                            last_updated = ''  # Listings API doesn't provide this
                            
                            # PERFORMANCE: Catalog enrichment disabled due to missing permissions
                            # The Listings API already provides product_name from summaries (sufficient for core functionality)
                            # Catalog API calls were failing with 403 Unauthorized, causing massive slowdown
                            # To re-enable: Add catalog:read permissions to Amazon API role and remove this comment
                            # if asin:
                            #     try:
                            #         catalog_client = CatalogItems(...)
                            #         ...enrichment code...
                            #     except Exception as catalog_error:
                            #         self.logger.warning(f"Could not fetch catalog details for ASIN {asin}: {str(catalog_error)}")
                            
                            # Build comprehensive description
                            description_parts = [f'Amazon Product - {condition}']
                            if product_brand:
                                description_parts.append(f'Brand: {product_brand}')
                            if product_manufacturer and product_manufacturer != product_brand:
                                description_parts.append(f'Mfr: {product_manufacturer}')
                            if product_description:
                                description_parts.append(product_description[:200])  # Limit length
                            
                            # Use original Amazon seller SKU (no suffix)
                            # Fulfillment type is tracked via warehouse location field only
                            
                            # Create inventory item with data from Listings API
                            imported_items.append({
                                # Core identifiers
                                'sku': sku,  # Original Amazon seller SKU
                                'asin': asin,  # Amazon Standard Identification Number
                                'fnsku': fn_sku,  # Fulfillment Network SKU (FBA barcode if FBA, or ASIN)
                                'listing_id': sku,  # Original Amazon seller SKU (for marketplace linking)
                                
                                # Product information
                                'name': product_name,
                                'description': ' | '.join(description_parts),
                                'brand': product_brand,
                                'manufacturer': product_manufacturer,
                                'category': product_category,
                                
                                # Quantity - from fulfillmentAvailability
                                'quantity': quantity,
                                
                                # Metadata
                                'fulfillment_type': fulfillment_type,
                                'condition': condition,
                                'last_updated': last_updated,
                                'price': product_price,
                                
                                # Additional data (for future use)
                                'images': product_images,
                                'dimensions': product_dimensions,
                                'weight': product_weight
                            })

                            # BT38 FBA STORAGE BRANCH
                            # Listings API already detects FBA above.
                            # This stores FBA stock separately in amazon_fba_inventory
                            # so View Products / MCF can read Amazon-held stock without treating it as pushable FBM stock.
                            if fulfillment_type == "FBA":
                                try:
                                    from app import db
                                    from sqlalchemy import text
                                    from datetime import datetime

                                    now = datetime.utcnow()

                                    existing_fba = db.session.execute(text("""
                                        SELECT id
                                        FROM amazon_fba_inventory
                                        WHERE store_id = :store_id
                                          AND seller_sku = :seller_sku
                                        LIMIT 1
                                    """), {
                                        "store_id": store.id,
                                        "seller_sku": sku,
                                    }).fetchone()

                                    fba_params = {
                                        "store_id": store.id,
                                        "seller_sku": sku,
                                        "asin": asin,
                                        "fnsku": fn_sku,
                                        "title": product_name,
                                        "condition": condition,
                                        "available_quantity": int(quantity or 0),
                                        "reserved_quantity": 0,
                                        "inbound_quantity": 0,
                                        "last_synced_at": now,
                                        "last_sync_status": "success",
                                        "is_active": True,
                                        "is_archived": False,
                                        "updated_at": now,
                                        "created_at": now,
                                    }

                                    if existing_fba:
                                        fba_params["id"] = existing_fba.id
                                        db.session.execute(text("""
                                            UPDATE amazon_fba_inventory
                                            SET asin = :asin,
                                                fnsku = :fnsku,
                                                title = :title,
                                                condition = :condition,
                                                available_quantity = :available_quantity,
                                                reserved_quantity = :reserved_quantity,
                                                inbound_quantity = :inbound_quantity,
                                                last_synced_at = :last_synced_at,
                                                last_sync_status = :last_sync_status,
                                                is_active = :is_active,
                                                is_archived = :is_archived,
                                                updated_at = :updated_at
                                            WHERE id = :id
                                        """), fba_params)
                                    else:
                                        db.session.execute(text("""
                                            INSERT INTO amazon_fba_inventory (
                                                store_id,
                                                seller_sku,
                                                asin,
                                                fnsku,
                                                title,
                                                condition,
                                                available_quantity,
                                                reserved_quantity,
                                                inbound_quantity,
                                                last_synced_at,
                                                last_sync_status,
                                                is_active,
                                                is_archived,
                                                created_at,
                                                updated_at
                                            ) VALUES (
                                                :store_id,
                                                :seller_sku,
                                                :asin,
                                                :fnsku,
                                                :title,
                                                :condition,
                                                :available_quantity,
                                                :reserved_quantity,
                                                :inbound_quantity,
                                                :last_synced_at,
                                                :last_sync_status,
                                                :is_active,
                                                :is_archived,
                                                :created_at,
                                                :updated_at
                                            )
                                        """), fba_params)

                                    db.session.commit()
                                    self.logger.info(f"Stored FBA inventory row: {sku} qty={quantity}")

                                except Exception as fba_store_error:
                                    db.session.rollback()
                                    self.logger.error(f"FBA STORAGE ERROR for {sku}: {str(fba_store_error)}")

                            
                        except Exception as item_error:
                            self.logger.warning(f"Error processing inventory item: {str(item_error)}")
                            continue
                    
                    # Check for next page - Python SP-API library stores nextToken as response.next_token (object attribute)
                    # NOT in response.payload dict!
                    page_token = None
                    
                    # Check response object attributes first (Python SP-API library format)
                    if hasattr(response, 'next_token') and response.next_token:
                        page_token = response.next_token
                        self.logger.debug(f"Found next_token on response object: {page_token[:50]}...")
                    elif hasattr(response, 'pagination') and response.pagination:
                        # Pagination object might contain nextToken
                        if isinstance(response.pagination, dict) and 'nextToken' in response.pagination:
                            page_token = response.pagination['nextToken']
                            self.logger.debug(f"Found nextToken in response.pagination dict: {page_token[:50]}...")
                    # Fall back to checking payload (for compatibility)
                    elif 'nextToken' in response.payload:
                        page_token = response.payload.get('nextToken')
                        self.logger.debug(f"Found nextToken in response.payload: {page_token[:50]}...")
                    elif 'pagination' in response.payload:
                        pagination = response.payload.get('pagination', {})
                        page_token = pagination.get('nextToken')
                        self.logger.debug(f"Found nextToken in response.payload.pagination: {page_token[:50]}...")
                    
                    if not page_token:
                        self.logger.info(f"No more pages - completed Amazon import after {page_count} pages")
                        break
                    else:
                        self.logger.info(f"Found next page token, fetching page {page_count + 1}...")
                else:
                    self.logger.warning(f"No payload in response for page {page_count}")
                    break
            
            self.logger.info(f"Total items imported from Amazon: {len(imported_items)} across {page_count} pages")
            return True, imported_items, f"Successfully imported {len(imported_items)} items from Amazon ({page_count} pages)"
            
        except Exception as e:
            error_msg = f"Error importing inventory from Amazon: {str(e)}"
            self.logger.error(error_msg)
            return False, [], error_msg
    
    def sync_inventory_to_amazon(self, store: Store, item: InventoryItem) -> Tuple[bool, str]:
        """
        Sync a single inventory item to Amazon using Feeds API
        Returns (success, message)
        """
        try:
            if not store.api_key:
                return False, "No API credentials configured"
                
            if not AMAZON_SP_API_AVAILABLE:
                self.logger.warning(f"Amazon SP-API not available, simulating sync for {item.sku}")
                return True, f"Simulated sync for {item.name} (SKU: {item.sku}) - quantity: {item.quantity}"
                
            import json
            from models import WarehouseStock
            creds = json.loads(store.api_key)
            
            # Validate required credentials
            required_keys = ['refresh_token', 'lwa_app_id', 'lwa_client_secret']
            missing_keys = [key for key in required_keys if not creds.get(key)]
            if missing_keys:
                return False, f"Missing required credentials: {', '.join(missing_keys)}"
            
            # Initialize credentials dict
            credentials = {
                'refresh_token': creds['refresh_token'],
                'lwa_app_id': creds['lwa_app_id'],
                'lwa_client_secret': creds['lwa_client_secret']
            }
            
            # Initialize both Inventories and Feeds clients
            inventory_client = Inventories(
                marketplace=self.marketplace,
                credentials=credentials
            )
            feeds_client = Feeds(
                marketplace=self.marketplace,
                credentials=credentials
            )
            
            marketplace_ids = [self.marketplace.marketplace_id]
            
            # Check if this is an FBA or FBM item by looking at warehouse location
            warehouse_stock = WarehouseStock.query.filter_by(sku=item.sku).first()
            is_fba_item = warehouse_stock and warehouse_stock.location and 'FBA' in warehouse_stock.location
            is_fbm_item = warehouse_stock and warehouse_stock.location and 'FBM' in warehouse_stock.location
            
            # For FBA items, verify they exist in Amazon FBA inventory before pushing
            # For FBM items, skip this check since FBA API won't find them
            if is_fba_item:
                try:
                    self.logger.info(f"Verifying FBA product exists on Amazon for SKU: {item.sku}")
                    inventory_response = inventory_client.get_inventory_summary_marketplace(
                        granularityType='Marketplace',
                        granularityId=self.marketplace.marketplace_id,
                        marketplaceIds=marketplace_ids,
                        sellerSkus=[item.sku]
                    )
                    
                    if not (inventory_response.payload and 'inventorySummaries' in inventory_response.payload):
                        return False, "Unable to retrieve inventory from Amazon FBA"
                        
                    summaries = inventory_response.payload['inventorySummaries']
                    if not summaries:
                        return False, f"FBA product {item.sku} not found in Amazon FBA inventory. Cannot update quantity for non-existent product."
                        
                    # Product exists, proceed with inventory update
                    self.logger.info(f"Found existing FBA product on Amazon for SKU: {item.sku}")
                except Exception as fba_check_error:
                    self.logger.error(f"Error verifying FBA product {item.sku}: {str(fba_check_error)}")
                    return False, f"FBA verification error: {str(fba_check_error)}"
            elif is_fbm_item:
                self.logger.info(f"Skipping FBA verification for FBM item: {item.sku} - proceeding with feed update")
            else:
                self.logger.warning(f"Unknown fulfillment type for SKU: {item.sku} (location: {warehouse_stock.location if warehouse_stock else 'None'}) - attempting feed update")
            
            # Proceed with inventory feed update
            try:
                
                # Generate inventory feed XML
                xml_content = self._generate_inventory_feed_xml(item)
                
                # Replace placeholder with actual seller ID if available
                seller_id = creds.get('seller_id', 'MERCHANT_ID')
                xml_content = xml_content.replace('MERCHANT_ID_PLACEHOLDER', seller_id)
                
                # Log full XML for debugging FATAL feeds
                self.logger.info(f"Generated XML feed for {item.sku} (seller_id={seller_id}): {xml_content}")
                
                # Create feed document and upload content
                success, message, feed_document_id = self._create_feed_document(feeds_client, xml_content)
                if not success:
                    return False, f"Feed document creation failed: {message}"
                    
                self.logger.info(f"Created feed document {feed_document_id} for SKU {item.sku}")
                
                # Create the feed with throttling and retry
                try:
                    # Enforce throttle before submission
                    _throttle_feed_submission(self.marketplace.marketplace_id)
                    
                    # Retry loop with backoff on QuotaExceeded
                    create_feed_response = None
                    for attempt_idx, backoff_delay in enumerate([0] + AMAZON_RETRY_BACKOFF):
                        try:
                            if attempt_idx > 0:
                                jitter = random.uniform(0, AMAZON_JITTER_MAX)
                                total_delay = backoff_delay + jitter
                                logging.warning(f"Amazon feed throttled; backing off for {total_delay:.1f}s (attempt {attempt_idx}/{len(AMAZON_RETRY_BACKOFF)})")
                                time.sleep(total_delay)
                            
                            # BT38: remove missing price fields before Amazon call; stock-only push is allowed

                            
                            if "payload" in locals():

                            
                                payload = _bt38_strip_missing_price_fields(payload)

                            
                            create_feed_response = feeds_client.create_feed(
                                feed_type='POST_INVENTORY_AVAILABILITY_DATA',
                                marketplace_ids=marketplace_ids,
                                input_feed_document_id=feed_document_id
                            )
                            
                            # Check for errors in response
                            if hasattr(create_feed_response, 'errors') and create_feed_response.errors:
                                errors = create_feed_response.errors
                                if any(e.get('code') == 'QuotaExceeded' for e in errors):
                                    if attempt_idx >= len(AMAZON_RETRY_BACKOFF):
                                        return False, f"Feed creation failed after {len(AMAZON_RETRY_BACKOFF)} retries: QuotaExceeded"
                                    continue  # Retry
                                else:
                                    return False, f"Feed creation error: {errors}"
                            
                            # Success - update timestamp
                            _update_feed_timestamp(self.marketplace.marketplace_id)
                            break
                            
                        except Exception as e:
                            error_str = str(e)
                            if 'QuotaExceeded' in error_str or '429' in error_str:
                                if attempt_idx >= len(AMAZON_RETRY_BACKOFF):
                                    return False, f"Feed creation failed after {len(AMAZON_RETRY_BACKOFF)} retries: {error_str}"
                                continue  # Retry
                            else:
                                raise  # Non-quota error, don't retry
                    
                    if not create_feed_response or not create_feed_response.payload:
                        return False, "Failed to create inventory feed"
                        
                    feed_id = create_feed_response.payload.get('feedId')
                    if not feed_id:
                        return False, "No feed ID returned from Amazon"
                        
                    self.logger.info(f"Created feed {feed_id} for SKU {item.sku}")
                    
                    # Create FeedStatus record for tracking
                    from models import FeedStatus
                    from app import db as app_db
                    feed_status = FeedStatus(
                        store_id=store.id,
                        feed_id=feed_id,
                        feed_type='POST_INVENTORY_AVAILABILITY_DATA',
                        processing_status='IN_QUEUE',
                        sku=item.sku,
                        quantity_pushed=item.quantity
                    )
                    app_db.session.add(feed_status)
                    app_db.session.commit()
                    self.logger.info(f"Created FeedStatus record for feed {feed_id}")
                    
                    # Feed created successfully - return immediately without waiting
                    # Feed will process asynchronously on Amazon's side  
                    # Background service will check status and update results
                    self.logger.info(f"Successfully submitted inventory update for {item.sku} (feed {feed_id})")
                    return True, f"Successfully synced {item.name} (SKU: {item.sku}) to Amazon. Feed {feed_id} submitted for processing - status will update automatically."
                        
                except Exception as feed_error:
                    self.logger.error(f"Error creating feed for SKU {item.sku}: {str(feed_error)}")
                    return False, f"Feed creation error: {str(feed_error)}"
                    
            except Exception as api_error:
                self.logger.error(f"Amazon API error for SKU {item.sku}: {str(api_error)}")
                return False, f"API error: {str(api_error)}"
                
        except json.JSONDecodeError:
            return False, "Invalid JSON format in API credentials"
        except Exception as e:
            self.logger.error(f"Error syncing item {item.sku} to Amazon: {str(e)}")
            return False, f"Sync error: {str(e)}"
    
    def get_amazon_inventory(self, store: Store) -> List[Dict]:
        """
        Retrieve current inventory from Amazon
        Returns list of inventory items from Amazon
        """
        try:
            if not store.api_key:
                return []
                
            import json
            creds = json.loads(store.api_key)
            
            # Initialize client with credentials dict  
            credentials = {
                'refresh_token': creds['refresh_token'],
                'lwa_app_id': creds['lwa_app_id'],
                'lwa_client_secret': creds['lwa_client_secret']
            }
            inventory_client = Inventories(
                marketplace=self.marketplace,
                credentials=credentials
            )
            
            # Get all inventory summaries using correct SP-API method
            response = inventory_client.get_inventory_summary_marketplace(
                granularityType='Marketplace',
                granularityId=self.marketplace.marketplace_id,
                marketplaceIds=[self.marketplace.marketplace_id]
            )
            
            amazon_inventory = []
            if hasattr(response, 'payload') and response.payload and 'inventorySummaries' in response.payload:
                summaries = response.payload.get('inventorySummaries', [])
                if summaries:  # Check if summaries is not None and not empty
                    for summary in summaries:
                        amazon_inventory.append({
                            'sku': summary.get('sellerSku', ''),
                            'asin': summary.get('asin', ''),
                            'quantity': summary.get('totalQuantity', 0),
                            'condition': summary.get('condition', 'New'),
                            'last_updated': datetime.utcnow()
                        })
            
            self.logger.info(f"Retrieved {len(amazon_inventory)} items from Amazon store: {store.name}")
            return amazon_inventory
            
        except Exception as e:
            self.logger.error(f"Error retrieving Amazon inventory for store {store.name}: {str(e)}")
            return []
    
    def create_amazon_credentials_template(self, region='US') -> Dict:
        """
        Returns template for Amazon SP-API credentials
        """
        # European marketplace IDs
        marketplace_ids = {
            'US': 'ATVPDKIKX0DER',
            'UK': 'A1F83G8C2ARO7P', 
            'DE': 'A1PA6795UKMFR9',
            'FR': 'A13V1IB3VIYZZH',
            'IT': 'APJ6JRA9NG5V4',
            'ES': 'A1RKKUPIHCS9HS',
            'CA': 'A2EUQ1WTGCTBG2'
        }
        
        return {
            "refresh_token": "",
            "lwa_app_id": "", 
            "lwa_client_secret": "",
            "marketplace_id": marketplace_ids.get(region, marketplace_ids['US']),
            "seller_id": "",
            "region": region
        }
    
    def validate_credentials_format(self, api_key: str) -> Tuple[bool, str]:
        """
        Validate that API key contains required Amazon credentials
        """
        try:
            creds = json.loads(api_key)
            required_keys = ['refresh_token', 'lwa_app_id', 'lwa_client_secret']
            missing_keys = [key for key in required_keys if not creds.get(key)]
            
            if missing_keys:
                return False, f"Missing required fields: {', '.join(missing_keys)}"
                
            return True, "Credentials format is valid"
            
        except json.JSONDecodeError:
            return False, "Invalid JSON format for API credentials"
        except Exception as e:
            return False, f"Error validating credentials: {str(e)}"
    
    def get_auth_diagnostics(self, store: Store) -> Dict:
        """
        Return diagnostic info about marketplace/region and SP-API auth state
        
        Args:
            store: Store object with Amazon credentials
            
        Returns:
            Dictionary with marketplace_id, region, host, and auth status
        """
        info = {
            "marketplace_id": getattr(self.marketplace, "marketplace_id", None),
            "seller_id": None
        }
        
        # Get seller ID from store credentials
        try:
            creds = json.loads(store.api_key)
            info["seller_id"] = creds.get("seller_id")
        except Exception:
            pass
        
        # Resolve region and host
        marketplace_id = info["marketplace_id"]
        if marketplace_id:
            region, host = resolve_region_host(marketplace_id)
            info.update({"resolved_region": region, "host": host})
        else:
            info.update({"resolved_region": "unknown", "host": "unknown"})
        
        # Test auth by attempting connection
        try:
            success = self.authenticate_store(store)
            info["scopes_ok"] = success
            if not success:
                info["raw_error"] = "Authentication failed - check credentials"
        except Exception as e:
            info["scopes_ok"] = False
            info["raw_error"] = str(e)
        
        return info
    
    def get_last_feed_ids_for_sku(self, sku: str, limit: int = 3) -> List[Dict]:
        """
        Return recent feed IDs and timestamps for this SKU if available.
        Try DB first (SyncLog or MarketplaceListing state if present),
        else parse the latest /tmp/logs/Start_application_*.log for lines containing the SKU and 'feedId' or 'Created feed'.
        
        Args:
            sku: SKU to search for
            limit: Maximum number of feed IDs to return
            
        Returns:
            List of dicts with feed_id and created timestamp
        """
        import re
        import glob
        
        feeds = []
        
        # Try DB first - check SyncLog for recent Amazon feed submissions
        try:
            from models import SyncLog
            sync_logs = SyncLog.query.filter(
                SyncLog.details.like(f'%{sku}%'),
                SyncLog.details.like('%feedId%')
            ).order_by(SyncLog.timestamp.desc()).limit(limit).all()
            
            for log in sync_logs:
                # Extract feed ID from details JSON
                try:
                    details = json.loads(log.details) if isinstance(log.details, str) else log.details
                    if isinstance(details, dict) and 'feedId' in details:
                        feeds.append({
                            "feed_id": details['feedId'],
                            "created": log.timestamp.isoformat() if log.timestamp else None
                        })
                except Exception:
                    pass
        except Exception as e:
            logging.debug(f"DB lookup for feeds failed: {e}")
        
        # If not found in DB, parse logs
        if not feeds:
            try:
                log_files = sorted(glob.glob('/tmp/logs/Start_application_*.log'), reverse=True)
                for log_file in log_files[:3]:  # Check last 3 log files
                    with open(log_file, 'r') as f:
                        for line in f:
                            if sku in line and ('feedId' in line or 'Created feed' in line):
                                # Extract feed ID using regex
                                match = re.search(r'feedId[\'"]?\s*[:=]\s*[\'"]?([a-zA-Z0-9\-]+)', line)
                                if match:
                                    feeds.append({
                                        "feed_id": match.group(1),
                                        "created": "unknown"
                                    })
                                    if len(feeds) >= limit:
                                        break
                    if len(feeds) >= limit:
                        break
            except Exception as e:
                logging.debug(f"Log file parsing failed: {e}")
        
        return feeds[:limit]
    
    def get_feed_report(self, feed_id: str) -> Dict:
        """
        Call getFeed(feed_id). If processingReport/resultDocumentId exists, fetch it via getFeedDocument and return parsed summary.
        
        Args:
            feed_id: Amazon feed ID to retrieve report for
            
        Returns:
            Dict with feedId, processingStatus, summary, and errors
        """
        import gzip
        import requests as req
        
        result = {
            "feedId": feed_id,
            "processingStatus": "UNKNOWN",
            "summary": {},
            "errors": []
        }
        
        if not AMAZON_SP_API_AVAILABLE:
            result["errors"].append("SP-API not available")
            return result
        
        try:
            feeds_client = self._get_feeds_client()
            if not feeds_client:
                result["errors"].append("Could not initialize Feeds client")
                return result
            
            # Get feed status
            response = feeds_client.get_feed(feed_id)
            feed_data = response.payload
            
            result["processingStatus"] = feed_data.get("processingStatus", "UNKNOWN")
            
            # If there's a result document, fetch it
            result_doc_id = feed_data.get("resultFeedDocumentId")
            if result_doc_id:
                try:
                    doc_response = feeds_client.get_feed_document(result_doc_id)
                    doc_data = doc_response.payload
                    doc_url = doc_data.get("url")
                    compression = doc_data.get("compressionAlgorithm")
                    
                    if doc_url:
                        # Download document
                        doc_content = req.get(doc_url).content
                        
                        # Decompress if needed
                        if compression == "GZIP":
                            doc_content = gzip.decompress(doc_content)
                        
                        # Parse as JSON or XML
                        try:
                            doc_text = doc_content.decode('utf-8')
                            # Try JSON first
                            try:
                                parsed = json.loads(doc_text)
                                result["summary"] = parsed
                            except json.JSONDecodeError:
                                # Try XML parsing
                                import xml.etree.ElementTree as ET
                                root = ET.fromstring(doc_text)
                                # Extract errors from XML
                                for msg in root.findall(".//{http://www.amazon.com/merchants/seller-central/feeds}Message"):
                                    msg_code = msg.find("{http://www.amazon.com/merchants/seller-central/feeds}MessageCode")
                                    msg_text = msg.find("{http://www.amazon.com/merchants/seller-central/feeds}Message")
                                    if msg_code is not None:
                                        result["errors"].append({
                                            "code": msg_code.text,
                                            "message": msg_text.text if msg_text is not None else ""
                                        })
                                result["summary"]["raw_xml"] = doc_text[:500]  # First 500 chars
                        except Exception as parse_err:
                            result["summary"]["raw_text"] = str(doc_content)[:500]
                            result["errors"].append(f"Parse error: {str(parse_err)}")
                
                except Exception as doc_err:
                    result["errors"].append(f"Document fetch error: {str(doc_err)}")
        
        except Exception as e:
            result["errors"].append(f"Feed fetch error: {str(e)}")
        
        return result
    
    def prevalidate_sku(self, store: 'Store', sku: str) -> dict:
        """
        Returns a dict with checks used before feed submission:
        { "sku": sku, "ok": bool, "reasons": [..], "marketplace_id": ..., "region_host": ..., 
          "asin": "...?" , "price": float|None, "fulfillment": "FBM|FBA|unknown", 
          "computed_qty": int, "xml_preview": "<AmazonEnvelope...> (first 500 chars)" }
        - Compute available qty using whatever the service currently uses.
        - Price missing/zero → reason
        - ASIN missing → reason
        - Marketplace/region mismatch (UK should be EU host) → reason
        - Build the exact XML envelope that would be sent and include a 500-char preview.
        - Never raise; return ok=False with reasons on failure.
        """
        import traceback
        from models import InventoryItem, WarehouseStock, MarketplaceListing
        from extensions import db
        
        result = {
            "sku": sku,
            "ok": True,
            "reasons": [],
            "marketplace_id": None,
            "region": None,
            "region_host": None,
            "asin": None,
            "price": None,
            "fulfillment": "unknown",
            "computed_qty": 0,
            "xml_preview": ""
        }
        
        try:
            # Get marketplace info
            result["marketplace_id"] = self.marketplace.marketplace_id if self.marketplace else None
            if result["marketplace_id"]:
                region, host = resolve_region_host(result["marketplace_id"])
                result["region"] = region
                result["region_host"] = host
            
            # Find inventory item
            item = db.session.query(InventoryItem).filter_by(sku=sku).first()
            if not item:
                result["ok"] = False
                result["reasons"].append(f"SKU {sku} not found in inventory")
                return result
            
            # Find warehouse stock
            warehouse_stock = db.session.query(WarehouseStock).filter_by(sku=sku).first()
            if warehouse_stock:
                result["computed_qty"] = max(0, warehouse_stock.available_quantity or 0)
                # Determine fulfillment type from location
                if warehouse_stock.location:
                    if 'FBA' in warehouse_stock.location.upper():
                        result["fulfillment"] = "FBA"
                    elif 'FBM' in warehouse_stock.location.upper():
                        result["fulfillment"] = "FBM"
            else:
                result["computed_qty"] = max(0, item.quantity or 0)
            
            # Find marketplace listing
            if store:
                listing = db.session.query(MarketplaceListing).filter_by(
                    store_id=store.id,
                    external_sku=sku
                ).first()
                
                if listing:
                    result["asin"] = listing.asin
                    result["price"] = listing.price
                    
                    # Validate price
                    if not result["price"] or result["price"] <= 0:
                        result["ok"] = False
                        result["reasons"].append(f"Price missing or zero (price={result['price']})")
                    
                    # Validate ASIN
                    if not result["asin"]:
                        result["ok"] = False
                        result["reasons"].append("ASIN missing")
                else:
                    result["ok"] = False
                    result["reasons"].append(f"No marketplace listing found for SKU {sku} in this store")
                    return result
            
            # Generate XML preview if validation passed
            if result["ok"]:
                try:
                    xml_content = self._generate_inventory_feed_xml(item)
                    result["xml_preview"] = xml_content[:500]
                except Exception as xml_err:
                    result["ok"] = False
                    result["reasons"].append(f"XML generation failed: {str(xml_err)}")
            
        except Exception as e:
            result["ok"] = False
            result["reasons"].append(f"Prevalidation error: {str(e)}")
            logging.error(f"Prevalidation error for {sku}: {traceback.format_exc()}")
        
        return result
    
    def bulk_push_safe(self, store: 'Store', skus: list, dry_run: bool = False) -> dict:
        """
        Runs bulk push with detailed error capture.
        Returns:
          { "ok": bool,
            "started_at": iso, "finished_at": iso,
            "dry_run": dry_run,
            "items": [ { "sku":..., "precheck": {...}, "pushed": bool, "error": "...?", "feed_id": "...?" } ],
            "top_error": "...?" }
        """
        import traceback
        from models import InventoryItem
        from extensions import db
        
        out = {
            "ok": True,
            "dry_run": dry_run,
            "items": [],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        
        try:
            for sku in skus:
                pre = self.prevalidate_sku(store, sku)
                rec = {"sku": sku, "precheck": pre}
                
                if not pre.get("ok"):
                    rec["pushed"] = False
                    rec["error"] = "prevalidation_failed"
                    out["items"].append(rec)
                    continue
                
                if dry_run:
                    rec["pushed"] = False
                    out["items"].append(rec)
                    continue
                
                # Real push using existing sync method
                try:
                    item = db.session.query(InventoryItem).filter_by(sku=sku).first()
                    if not item:
                        rec["pushed"] = False
                        rec["error"] = "Item not found for push"
                        out["items"].append(rec)
                        continue
                    
                    # Use sync_inventory_to_amazon method
                    success, message = self.sync_inventory_to_amazon(store, item)
                    rec["pushed"] = success
                    rec["message"] = message
                    
                    if not success:
                        rec["error"] = message
                    
                except Exception as e:
                    rec["pushed"] = False
                    rec["error"] = f"{e}"
                    logging.error(f"Push error for {sku}: {traceback.format_exc()}")
                
                out["items"].append(rec)
        
        except Exception as e:
            out["ok"] = False
            out["top_error"] = f"{e}\n" + traceback.format_exc()
            logging.error(f"Bulk push error: {traceback.format_exc()}")
        
        out["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        return out
    
    def get_feed_status(self, store: 'Store', feed_id: str) -> dict:
        """
        Calls Feeds API getFeed(feed_id). Return feed status details.
        Never raises; returns error dict on failure.
        """
        result = {
            "feedId": feed_id,
            "processingStatus": "UNKNOWN",
            "createdTime": None,
            "processingStartTime": None,
            "processingEndTime": None,
            "resultDocumentId": None
        }
        
        if not AMAZON_SP_API_AVAILABLE:
            result["error"] = "SP-API not available"
            return result
        
        try:
            # Parse credentials
            if not store.api_key:
                result["error"] = "No API credentials configured"
                return result
            
            creds = json.loads(store.api_key)
            credentials = {
                'refresh_token': creds.get('refresh_token', ''),
                'lwa_app_id': creds.get('lwa_app_id', ''),
                'lwa_client_secret': creds.get('lwa_client_secret', '')
            }
            
            # Initialize Feeds client
            feeds_client = Feeds(
                marketplace=self.marketplace,
                credentials=credentials
            )
            
            response = feeds_client.get_feed(feed_id)
            feed_data = response.payload
            
            result["processingStatus"] = feed_data.get("processingStatus", "UNKNOWN")
            result["createdTime"] = feed_data.get("createdTime")
            result["processingStartTime"] = feed_data.get("processingStartTime")
            result["processingEndTime"] = feed_data.get("processingEndTime")
            result["resultDocumentId"] = feed_data.get("resultFeedDocumentId")
            
        except Exception as e:
            result["error"] = f"Failed to fetch feed status: {str(e)}"
            logging.error(f"Error fetching feed {feed_id}: {str(e)}")
        
        return result
    
    def get_feed_processing_report(self, store: 'Store', feed_id: str) -> dict:
        """
        If resultDocumentId exists, download via getFeedDocument, decompress if needed,
        parse the processing report and return a summary.
        Never raises; returns error dict on failure.
        """
        import gzip
        import requests as req
        
        result = {
            "feedId": feed_id,
            "processingStatus": "UNKNOWN",
            "summary": {},
            "errors": [],
            "raw": ""
        }
        
        if not AMAZON_SP_API_AVAILABLE:
            result["error"] = "SP-API not available"
            return result
        
        try:
            # Parse credentials
            if not store.api_key:
                result["error"] = "No API credentials configured"
                return result
            
            creds = json.loads(store.api_key)
            credentials = {
                'refresh_token': creds.get('refresh_token', ''),
                'lwa_app_id': creds.get('lwa_app_id', ''),
                'lwa_client_secret': creds.get('lwa_client_secret', '')
            }
            
            # Initialize Feeds client
            feeds_client = Feeds(
                marketplace=self.marketplace,
                credentials=credentials
            )
            
            response = feeds_client.get_feed(feed_id)
            feed_data = response.payload
            
            result["processingStatus"] = feed_data.get("processingStatus", "UNKNOWN")
            
            # Get result document if available
            result_doc_id = feed_data.get("resultFeedDocumentId")
            if not result_doc_id:
                result["note"] = "No result document available yet (feed may still be processing)"
                return result
            
            # Download result document
            try:
                doc_response = feeds_client.get_feed_document(result_doc_id)
                doc_data = doc_response.payload
                doc_url = doc_data.get("url")
                compression = doc_data.get("compressionAlgorithm")
                
                if doc_url:
                    doc_content = req.get(doc_url).content
                    
                    # Decompress if needed
                    if compression == "GZIP":
                        doc_content = gzip.decompress(doc_content)
                    
                    # Parse as JSON or XML
                    doc_text = doc_content.decode('utf-8')
                    result["raw"] = doc_text[:1000]  # First 1000 chars
                    
                    # Try JSON first
                    try:
                        parsed = json.loads(doc_text)
                        result["summary"] = parsed
                    except json.JSONDecodeError:
                        # Try XML parsing
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(doc_text)
                        
                        # Extract messages from XML
                        for msg in root.findall(".//{http://www.amazon.com/merchants/seller-central/feeds}Message"):
                            msg_code_elem = msg.find("{http://www.amazon.com/merchants/seller-central/feeds}MessageCode")
                            msg_text_elem = msg.find("{http://www.amazon.com/merchants/seller-central/feeds}Message")
                            
                            if msg_code_elem is not None:
                                result["errors"].append({
                                    "code": msg_code_elem.text,
                                    "message": msg_text_elem.text if msg_text_elem is not None else ""
                                })
                        
                        result["summary"]["format"] = "xml"
                        result["summary"]["message_count"] = len(result["errors"])
            
            except Exception as doc_err:
                result["error"] = f"Document fetch error: {str(doc_err)}"
                logging.error(f"Error fetching document for feed {feed_id}: {str(doc_err)}")
        
        except Exception as e:
            result["error"] = f"Feed processing report error: {str(e)}"
            logging.error(f"Error getting processing report for feed {feed_id}: {str(e)}")
        
        return result
    
    def get_live_listing_state(self, store: 'Store', sku: str) -> dict:
        """
        Best-effort live check via SP-API for the marketplace configured for this store.
        Returns SKU's live marketplace state including quantity and price.
        Never raises.
        """
        result = {
            "sku": sku,
            "marketplace_id": None,
            "resolved_region": None,
            "host": None,
            "fbm_fba": "unknown",
            "live_qty": None,
            "price": None
        }
        
        if not AMAZON_SP_API_AVAILABLE:
            result["note"] = "SP-API not available"
            return result
        
        try:
            # Get marketplace info
            result["marketplace_id"] = self.marketplace.marketplace_id if self.marketplace else None
            if result["marketplace_id"]:
                region, host = resolve_region_host(result["marketplace_id"])
                result["resolved_region"] = region
                result["host"] = host
            
            # Parse credentials
            if not store.api_key:
                result["note"] = "No API credentials configured"
                return result
            
            creds = json.loads(store.api_key)
            credentials = {
                'refresh_token': creds.get('refresh_token', ''),
                'lwa_app_id': creds.get('lwa_app_id', '') or creds.get('client_id', ''),
                'lwa_client_secret': creds.get('lwa_client_secret', '') or creds.get('client_secret', '')
            }
            
            # Try Inventory API first (for quantity)
            try:
                inventory_client = Inventories(
                    marketplace=self.marketplace,
                    credentials=credentials
                )
                
                inv_response = inventory_client.get_inventory_summary_marketplace(
                    granularityType='Marketplace',
                    granularityId=result["marketplace_id"],
                    marketplaceIds=[result["marketplace_id"]],
                    sellerSkus=[sku]
                )
                
                if hasattr(inv_response, 'payload') and inv_response.payload:
                    summaries = inv_response.payload.get('inventorySummaries', [])
                    if summaries:
                        summary = summaries[0]
                        result["live_qty"] = summary.get('totalQuantity', 0)
                        
                        # Determine FBM/FBA from condition
                        condition = summary.get('condition', '')
                        if 'FBA' in condition or summary.get('fnSku'):
                            result["fbm_fba"] = "FBA"
                        else:
                            result["fbm_fba"] = "FBM"
            
            except Exception as inv_err:
                result["inventory_error"] = str(inv_err)
                logging.debug(f"Inventory API error for {sku}: {str(inv_err)}")
            
            # Try Listings API for price (if available)
            try:
                listings_client = ListingsItems(
                    marketplace=self.marketplace,
                    credentials=credentials
                )
                
                listings_response = listings_client.get_listings_item(
                    sellerId=creds.get('seller_id', ''),
                    sku=sku,
                    marketplaceIds=[result["marketplace_id"]]
                )
                
                if hasattr(listings_response, 'payload') and listings_response.payload:
                    # Extract price from listings data
                    summaries = listings_response.payload.get('summaries', [])
                    if summaries:
                        for summary in summaries:
                            offers = summary.get('offers', [])
                            if offers:
                                offer = offers[0]
                                price_info = offer.get('price', {})
                                if 'amount' in price_info:
                                    result["price"] = float(price_info['amount'])
                                break
            
            except Exception as list_err:
                result["listings_error"] = str(list_err)
                logging.debug(f"Listings API error for {sku}: {str(list_err)}")
        
        except Exception as e:
            result["error"] = f"Live listing check error: {str(e)}"
            logging.error(f"Error checking live state for {sku}: {str(e)}")
        
        return result
    
    # ==============================
    # Diagnostic: Feeds Scope Probe
    # ==============================
    def check_feeds_scope(self, store: 'Store') -> dict:
        """
        Try a harmless Feeds API call to detect 401/403 (Unauthorized/Forbidden),
        which indicates missing Feeds scope or invalid auth. Returns dict.
        """
        import logging
        try:
            # Parse credentials
            if not store.api_key:
                return {"ok": False, "http": 0, "error": "No API credentials configured"}
            
            creds = json.loads(store.api_key)
            credentials = {
                'refresh_token': creds.get('refresh_token', ''),
                'lwa_app_id': creds.get('lwa_app_id', ''),
                'lwa_client_secret': creds.get('lwa_client_secret', '')
            }
            
            # Initialize Feeds client
            feeds_client = Feeds(
                marketplace=self.marketplace,
                credentials=credentials
            )
            
            # Minimal call: list feeds with tight limit; should return 200 when scoped
            resp = feeds_client.get_feeds(pageSize=1)
            payload = getattr(resp, "payload", {}) or {}
            return {"ok": True, "http": 200, "note": "Feeds scope OK", "payload_keys": list(payload.keys())}
        except Exception as e:
            msg = str(e)
            # Common signatures: 401/403/Unauthorized
            code = 0
            if "401" in msg: 
                code = 401
            if "403" in msg or "Unauthorized" in msg: 
                code = 403 if code == 0 else code
            logging.error(f"Feeds scope probe failed: {msg}")
            return {"ok": False, "http": code or 0, "error": msg}

    # ==============================
    # Phase 1: Order Import for Auto-Sync Engine
    # ==============================
    def get_mfn_orders(self, store: 'Store', created_after: str = None, max_results: int = 100) -> Dict:
        """
        Fetch MFN (Merchant Fulfilled Network) orders from Amazon SP-API.
        Only fetches orders that are NOT fulfilled by Amazon (i.e., FBM orders).
        
        Args:
            store: Store object with Amazon credentials
            created_after: ISO 8601 timestamp to fetch orders after (e.g., '2025-01-01T00:00:00Z')
            max_results: Maximum number of orders to fetch (default 100)
        
        Returns:
            Dict with 'success', 'orders' list, and optional 'error' message
        """
        import requests
        from datetime import datetime, timedelta
        
        try:
            if not store.api_key:
                return {"success": False, "orders": [], "error": "No API credentials configured"}
            
            creds = json.loads(store.api_key)
            marketplace_id = creds.get('marketplace_id', 'A1F83G8C2ARO7P')  # Default to UK
            
            # Get LWA access token
            token_result = get_lwa_access_token(
                client_id=creds.get('lwa_app_id'),
                client_secret=creds.get('lwa_client_secret'),
                refresh_token=creds.get('refresh_token')
            )
            
            if 'error' in token_result:
                return {"success": False, "orders": [], "error": f"Auth error: {token_result['error']}"}
            
            access_token = token_result.get('access_token')
            if not access_token:
                return {"success": False, "orders": [], "error": "No access token in response"}
            
            # Default to last 24 hours if no created_after specified
            if not created_after:
                created_after = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Resolve region and host
            region, host = resolve_region_host(marketplace_id)
            
            # Call SP-API Orders API
            url = f"https://{host}/orders/v0/orders"
            headers = {
                'x-amz-access-token': access_token,
                'Content-Type': 'application/json'
            }
            params = {
                'MarketplaceIds': marketplace_id,
                'CreatedAfter': created_after,
                'FulfillmentChannels': 'MFN',  # Only Merchant Fulfilled orders
                'OrderStatuses': 'Unshipped,PartiallyShipped,Shipped',  # Relevant statuses
                'MaxResultsPerPage': min(max_results, 100)
            }
            
            logging.info(f"Fetching MFN orders for store {store.name} since {created_after}")
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                from amazon_auth import safe_parse_json, AmazonNonJsonResponseError
                try:
                    data = safe_parse_json(response, "MFN Orders fetch")
                except AmazonNonJsonResponseError as e:
                    logging.error(f"[AMAZON_NON_JSON_RESPONSE] Orders API: {e}")
                    return {"success": False, "orders": [], "error": f"Amazon returned HTML: {e.preview[:100]}"}
                orders_payload = data.get('payload', {})
                orders = orders_payload.get('Orders', [])
                
                # Process orders to extract relevant data
                processed_orders = []
                for order in orders:
                    order_id = order.get('AmazonOrderId', '')
                    order_status = order.get('OrderStatus', '')
                    purchase_date = order.get('PurchaseDate', '')
                    
                    # Fetch order items for this order
                    items = self._fetch_order_items(host, access_token, order_id)
                    
                    for item in items:
                        processed_orders.append({
                            'marketplace_order_id': order_id,
                            'marketplace_order_item_id': item.get('OrderItemId', ''),
                            'sku': item.get('SellerSKU', ''),
                            'quantity': item.get('QuantityOrdered', 0),
                            'quantity_shipped': item.get('QuantityShipped', 0),
                            'item_price': float(item.get('ItemPrice', {}).get('Amount', 0)),
                            'currency': item.get('ItemPrice', {}).get('CurrencyCode', 'GBP'),
                            'order_status': order_status,
                            'purchase_date': purchase_date,
                            'fulfillment_channel': 'MFN'
                        })
                
                logging.info(f"Fetched {len(processed_orders)} MFN order items for store {store.name}")
                return {"success": True, "orders": processed_orders, "error": None}
                
            elif response.status_code == 429:
                logging.warning(f"Rate limited on Orders API for store {store.name}")
                return {"success": False, "orders": [], "error": "Rate limited - try again later"}
            else:
                error_msg = f"Orders API returned {response.status_code}: {response.text[:500]}"
                logging.error(error_msg)
                return {"success": False, "orders": [], "error": error_msg}
                
        except Exception as e:
            logging.error(f"Error fetching MFN orders for store {store.name}: {str(e)}")
            return {"success": False, "orders": [], "error": str(e)}
    
    def _fetch_order_items(self, host: str, access_token: str, order_id: str) -> List[Dict]:
        """Fetch order items for a specific order"""
        import requests
        
        try:
            url = f"https://{host}/orders/v0/orders/{order_id}/orderItems"
            headers = {
                'x-amz-access-token': access_token,
                'Content-Type': 'application/json'
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                from amazon_auth import safe_parse_json, AmazonNonJsonResponseError
                try:
                    data = safe_parse_json(response, f"Order items {order_id}")
                except AmazonNonJsonResponseError as e:
                    logging.error(f"[AMAZON_NON_JSON_RESPONSE] Order items {order_id}: {e}")
                    return []
                return data.get('payload', {}).get('OrderItems', [])
            else:
                logging.warning(f"Failed to fetch items for order {order_id}: {response.status_code}")
                return []
                
        except Exception as e:
            logging.warning(f"Error fetching items for order {order_id}: {str(e)}")
            return []
