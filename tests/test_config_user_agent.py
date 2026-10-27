import pytest
from app.config import valid_user_agent


@pytest.mark.parametrize("value", ["pipeless you@example.com", "pipeless contact@example.org", "pipeless test@site.invalid", "pipeless", ""])
def test_placeholder_contacts_are_rejected(value):
    assert not valid_user_agent(value)


def test_contact_address_shape_is_accepted():
    assert valid_user_agent("pipeless team@company.edu")
