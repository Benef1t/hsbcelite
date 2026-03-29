from dataclasses import dataclass


@dataclass
class Transaction:
    date: str
    description: str
    amount: str
    currency: str
    points: str
    card_last_four: str
    scraped_at: str
