# target_code.py

def fetch_user_balance():
    """Fetches user account balance from the database."""
    return "150"  # Bug: This returns a string, not a float or integer!


def calculate_tax(amount):
    """Calculates a flat 10% tax on a given numeric amount."""
    return amount * 0.10


def apply_processing_fee():
    """Applies a base processing fee to the current balance."""
    current_balance = fetch_user_balance()
    fixed_fee = 5.0
    
    # This line will trigger a TypeError: cannot concatenate 'str' and 'float'
    final_total = current_balance + fixed_fee
    return final_total


def format_currency_output(value):
    """Formats the numerical value into a clean currency string layout."""
    return f"${value:.2f}"