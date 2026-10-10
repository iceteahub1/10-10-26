"""Professor-required SQLite tools; optional db_path enables isolated tests."""
from bistro.order_service import browse_menu, check_allergen, add_item, view_cart


def get_menu_by_category(category, *, db_path=None):
    return browse_menu(category, db_path=db_path)


def check_item_allergen(item_id, allergen, *, db_path=None):
    """Check a declared allergen token, not a guarantee against cross-contact."""
    return check_allergen(item_id, allergen, db_path=db_path)


def add_item_to_order(session_id, item_name, qty, instructions, *, db_path=None):
    return add_item(session_id, item_name, qty, instructions, db_path=db_path)


def view_current_order(session_id, *, db_path=None):
    return view_cart(session_id, db_path=db_path)
