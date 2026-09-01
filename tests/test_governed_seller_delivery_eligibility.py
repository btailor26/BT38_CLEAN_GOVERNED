from services.governed_seller_delivery_eligibility import (
    evaluate_seller_delivery,
    normalise_postcode,
)


def test_postcodes_are_normalised_before_matching():
    assert normalise_postcode("le1 3wu") == "LE1 3WU"
    assert normalise_postcode("LE13WU") == "LE1 3WU"


def test_within_radius_is_eligible():
    result = evaluate_seller_delivery(
        enabled=True,
        prime_sfp=False,
        origin_postcode="LE1 3WU",
        destination_postcode="LE2 1AA",
        radius_miles="10",
        origin_coordinates=(52.6369, -1.1398),
        destination_coordinates=(52.6220, -1.1190),
    )
    assert result.eligible is True
    assert result.reason == "within_delivery_radius"
    assert result.distance_miles is not None


def test_outside_radius_is_not_eligible():
    result = evaluate_seller_delivery(
        enabled=True,
        prime_sfp=False,
        origin_postcode="LE1 3WU",
        destination_postcode="B1 1AA",
        radius_miles="10",
        origin_coordinates=(52.6369, -1.1398),
        destination_coordinates=(52.4797, -1.9027),
    )
    assert result.eligible is False
    assert result.reason == "outside_delivery_radius"


def test_prime_sfp_is_always_blocked():
    result = evaluate_seller_delivery(
        enabled=True,
        prime_sfp=True,
        origin_postcode="LE1 3WU",
        destination_postcode="LE2 1AA",
        radius_miles="10",
        origin_coordinates=(52.6369, -1.1398),
        destination_coordinates=(52.6220, -1.1190),
    )
    assert result.eligible is False
    assert result.reason == "prime_sfp_blocked"


def test_missing_coordinates_never_guesses_eligibility():
    result = evaluate_seller_delivery(
        enabled=True,
        prime_sfp=False,
        origin_postcode="LE1 3WU",
        destination_postcode="LE2 1AA",
        radius_miles="10",
    )
    assert result.eligible is False
    assert result.reason == "postcode_coordinates_unavailable"
