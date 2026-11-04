"""Generates sample_data/sales.csv, a made-up store sales dataset used for the demo,
the tests, and the benchmark. The random seed is fixed, so the file is always the same.

Run with:
    python scripts/make_sample_data.py
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "sample_data" / "sales.csv"

PRODUCTS = [
    ("Trail Backpack", "Outdoor", 89.00),
    ("Insulated Bottle", "Outdoor", 24.50),
    ("Camping Lantern", "Outdoor", 39.99),
    ("Wireless Earbuds", "Electronics", 79.00),
    ("Portable Charger", "Electronics", 34.99),
    ("Smart Watch", "Electronics", 199.00),
    ("Yoga Mat", "Fitness", 29.00),
    ("Resistance Bands", "Fitness", 18.75),
    ("Running Shoes", "Fitness", 119.00),
]
REGIONS = ["North", "South", "East", "West"]
REGION_WEIGHTS = [30, 20, 25, 25]
CHANNELS = ["Online", "In store"]


def main() -> None:
    rng = random.Random(42)
    start = date(2025, 1, 1)
    rows = []
    for order_id in range(1001, 1601):
        name, category, price = rng.choice(PRODUCTS)
        order_date = start + timedelta(days=rng.randint(0, 364))
        units = rng.choices([1, 2, 3, 4, 5], weights=[45, 25, 15, 10, 5])[0]
        discount = rng.choices([0, 0.1, 0.2], weights=[70, 20, 10])[0]
        revenue = round(units * price * (1 - discount), 2)
        rows.append(
            {
                "Order ID": order_id,
                "Order Date": order_date.isoformat(),
                "Region": rng.choices(REGIONS, weights=REGION_WEIGHTS)[0],
                "Channel": rng.choice(CHANNELS),
                "Customer Type": rng.choices(["New", "Returning"], weights=[40, 60])[0],
                "Product": name,
                "Category": category,
                "Units": units,
                "Unit Price": f"{price:.2f}",
                "Discount": discount,
                "Revenue": f"{revenue:.2f}",
            }
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
