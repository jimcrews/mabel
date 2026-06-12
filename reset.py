import glob
import os

INBOX_TRANSACTIONS = """1111234522226789,1212343433335665,500.00
3212343433335755,2222123433331212,1000.00
3212343433335755,1111234522226789,320.50
1111234522221234,1212343433335665,25.60
"""

ACCOUNT_BALANCES = """1111234522226789,5000.00
1111234522221234,10000.00
2222123433331212,550.00
1212343433335665,1200.00
3212343433335755,50000.00
"""


def clear_dir(pattern: str) -> None:
    for path in glob.glob(pattern):
        os.remove(path)


clear_dir("data/inbox/*")
clear_dir("data/archive/*")
clear_dir("data/*_account_balances.csv")

for log_file in glob.glob("data/*_transaction_log.csv"):
    open(log_file, "w").close()

with open("data/inbox/mable_transactions.csv", "w") as f:
    f.write(INBOX_TRANSACTIONS)

with open("data/mable_account_balances.csv", "w") as f:
    f.write(ACCOUNT_BALANCES)

print("[INFO] Reset complete.")
