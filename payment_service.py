# payment_service.py
from user_service import UserService

class PaymentService:
    """
    Handles payment processing, invoice generation, balance checks, and currency formatting.
    This service connects with bank APIs to process charges, apply service/processing fees, 
    and maintain the transactional integrity of ledger records.
    """
    
    def __init__(self):
        self.default_currency = "USD"
        self.user_service = UserService()

    def calculate_tax(self, amount):
        """Calculates a flat 10% tax on a given numeric amount."""
        return amount * 0.10

    def apply_processing_fee(self):
        """Applies a base processing fee to the current balance fetched from user service."""
        current_balance = self.user_service.get_user_balance(42)
        fixed_fee = 5.0
        # This line will trigger a TypeError: unsupported operand type(s) for +: 'str' and 'float'
        final_total = current_balance + fixed_fee
        return final_total
