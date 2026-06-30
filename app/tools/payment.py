from __future__ import annotations

import uuid
from typing import Dict


def mock_payment(vendor: str, amount: float) -> Dict[str, str]:
    transaction_id = f"tx-{uuid.uuid4().hex[:12]}"
    return {
        "status": "success",
        "message": f"Paid {amount:.2f} to {vendor}",
        "transaction_id": transaction_id,
    }
