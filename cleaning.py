import logging
import os
import time
from pathlib import Path

import dotenv
import pandas as pd
from sqlalchemy import create_engine, text


dotenv.load_dotenv()

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=LOG_DIR / "transaction_summary.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filemode="a",
)


def get_engine():
    """Create a SQLAlchemy engine using the existing project .env values."""
    return create_engine(
        "mysql+pymysql://"
        f"{os.getenv('USER')}:{os.getenv('PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )


def clean_transactions(transactions_df):
    transactions_df = transactions_df.copy()

    transactions_df["timestamp"] = pd.to_datetime(
        transactions_df["timestamp"],
        errors="coerce",
    )

    transactions_df = transactions_df.dropna(subset=["device_id"])

    transactions_df = (
        transactions_df.sort_values(
            by="payee_bank_id",
            key=lambda x: x.isna() | x.eq("unknown"),
        )
        .drop_duplicates(subset="transaction_id", keep="first")
        .copy()
    )

    transactions_df["payee_bank_id"] = transactions_df["payee_bank_id"].fillna(
        "Unknown"
    )

    transactions_df["amount"] = pd.to_numeric(
        transactions_df["amount"],
        errors="coerce",
    )
    transactions_df = transactions_df.dropna(subset=["amount"])

    return transactions_df


def clean_banks(banks_df):
    banks_df = banks_df.copy()
    banks_df = banks_df[~banks_df["bank_id"].str.endswith("_DUP", na=False)].copy()

    return banks_df


def clean_stage_events(stage_events_df):
    stage_events_df = stage_events_df.copy()
    stage_events_df["error_code"] = stage_events_df["error_code"].fillna("No Error")

    return stage_events_df


def load_source_tables(engine):
    """Load only the source tables used by the cleaning and summary steps."""
    return {
        "transactions": pd.read_sql("SELECT * FROM transactions", engine),
        "banks": pd.read_sql("SELECT * FROM banks", engine),
        "stage_events": pd.read_sql("SELECT * FROM stage_events", engine),
    }


def clean_source_tables(tables):
    """Apply the same cleaning steps from explorating_data.ipynb."""
    return {
        "transactions": clean_transactions(tables["transactions"]),
        "banks": clean_banks(tables["banks"]),
        "stage_events": clean_stage_events(tables["stage_events"]),
    }


def ingest_clean_tables(cleaned_tables, engine):
    """Replace dirty source tables with cleaned versions in MySQL."""
    for table_name, df in cleaned_tables.items():
        logging.info("Writing cleaned table %s with %s rows", table_name, len(df))
        df.to_sql(name=table_name, con=engine, if_exists="replace", index=False)


def clean_database_tables(engine):
    """Apply the notebook cleaning logic directly in MySQL for large tables."""
    with engine.begin() as conn:
        dirty_transaction_rows = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM transactions
                WHERE device_id IS NULL
                    OR amount IS NULL
                    OR payee_bank_id IS NULL
                    OR NOT (
                        CAST(amount AS CHAR) REGEXP '^-?[0-9]+(\\\\.[0-9]+)?$'
                    )
                """
            )
        ).scalar()
        duplicate_transactions = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT transaction_id
                    FROM transactions
                    GROUP BY transaction_id
                    HAVING COUNT(*) > 1
                ) duplicate_transaction_ids
                """
            )
        ).scalar()

        if dirty_transaction_rows or duplicate_transactions:
            logging.info("Cleaning transactions table in MySQL.")
            conn.execute(text("DROP TABLE IF EXISTS transactions_clean"))
            conn.execute(text("DROP TABLE IF EXISTS transactions_old"))
            conn.execute(
                text(
                    """
                    CREATE TABLE transactions_clean AS
                    SELECT
                        transaction_id,
                        STR_TO_DATE(timestamp, '%Y-%m-%d %H:%i:%s') AS timestamp,
                        customer_id,
                        CAST(amount AS DECIMAL(12, 2)) AS amount,
                        txn_type,
                        payer_bank_id,
                        COALESCE(payee_bank_id, 'Unknown') AS payee_bank_id,
                        device_id,
                        final_status,
                        final_stage_reached
                    FROM (
                        SELECT
                            t.*,
                            ROW_NUMBER() OVER (
                                PARTITION BY transaction_id
                                ORDER BY
                                    CASE
                                        WHEN payee_bank_id IS NULL
                                            OR payee_bank_id = 'unknown'
                                        THEN 1
                                        ELSE 0
                                    END
                            ) AS row_num
                        FROM transactions t
                        WHERE device_id IS NOT NULL
                            AND amount IS NOT NULL
                            AND CAST(amount AS CHAR)
                                REGEXP '^-?[0-9]+(\\\\.[0-9]+)?$'
                    ) ranked_transactions
                    WHERE row_num = 1
                    """
                )
            )
            conn.execute(
                text(
                    """
                    RENAME TABLE
                        transactions TO transactions_old,
                        transactions_clean TO transactions
                    """
                )
            )
            conn.execute(text("DROP TABLE transactions_old"))
        else:
            logging.info("Transactions table is already clean; skipping rewrite.")

        duplicate_bank_rows = conn.execute(
            text("SELECT COUNT(*) FROM banks WHERE RIGHT(bank_id, 4) = '_DUP'")
        ).scalar()
        if duplicate_bank_rows:
            logging.info("Cleaning banks table in MySQL.")
            conn.execute(text("DROP TABLE IF EXISTS banks_clean"))
            conn.execute(text("DROP TABLE IF EXISTS banks_old"))
            conn.execute(
                text(
                    """
                    CREATE TABLE banks_clean AS
                    SELECT *
                    FROM banks
                    WHERE RIGHT(bank_id, 4) <> '_DUP'
                    """
                )
            )
            conn.execute(
                text(
                    """
                    RENAME TABLE
                        banks TO banks_old,
                        banks_clean TO banks
                    """
                )
            )
            conn.execute(text("DROP TABLE banks_old"))
        else:
            logging.info("Banks table is already clean; skipping rewrite.")

        stage_event_error_nulls = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM stage_events
                WHERE error_code IS NULL OR error_code = ''
                """
            )
        ).scalar()
        if stage_event_error_nulls:
            logging.info("Cleaning stage_events table in MySQL.")
            conn.execute(
                text(
                    """
                    UPDATE stage_events
                    SET error_code = 'No Error'
                    WHERE error_code IS NULL OR error_code = ''
                    """
                )
            )
        else:
            logging.info("Stage_events table is already clean; skipping update.")


def recreate_transaction_summary_table(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS transaction_summary"))
        conn.execute(
            text(
                """
                CREATE TABLE transaction_summary (
                    transaction_id VARCHAR(30),
                    customer_id VARCHAR(30),
                    timestamp DATETIME,
                    amount DECIMAL(12, 2),
                    transaction_type VARCHAR(20),
                    payer_bank_id VARCHAR(30),
                    payee_bank_id VARCHAR(30),
                    device_id VARCHAR(30),
                    final_stage_reached VARCHAR(10),

                    os VARCHAR(50),
                    network_type VARCHAR(30),

                    stage_id VARCHAR(10),
                    stage_name VARCHAR(100),
                    stage_status VARCHAR(20),

                    error_code VARCHAR(10),
                    error_description VARCHAR(255),
                    error_category VARCHAR(50),

                    payer_bank_name VARCHAR(100),
                    payer_bank_tier VARCHAR(20),

                    payee_bank_name VARCHAR(100),
                    payee_bank_tier VARCHAR(20),

                    PRIMARY KEY (transaction_id, stage_id)
                )
                """
            )
        )


def populate_transaction_summary(engine):
    insert_query = """
        INSERT INTO transaction_summary (
            transaction_id, customer_id, timestamp, amount, transaction_type,
            payer_bank_id, payee_bank_id, device_id, final_stage_reached,
            os, network_type,
            stage_id, stage_name, stage_status,
            error_code, error_description, error_category,
            payer_bank_name, payer_bank_tier,
            payee_bank_name, payee_bank_tier
        )
        SELECT
            t.transaction_id,
            t.customer_id,
            t.timestamp,
            t.amount,
            t.txn_type AS transaction_type,
            t.payer_bank_id,
            t.payee_bank_id,
            t.device_id,
            t.final_stage_reached,

            d.os,
            d.network_type,

            se.stage_id,
            fs.stage_name,
            se.stage_status,

            se.error_code,
            ec.error_description,
            ec.error_category,

            pb.bank_name AS payer_bank_name,
            pb.bank_tier AS payer_bank_tier,

            pyb.bank_name AS payee_bank_name,
            pyb.bank_tier AS payee_bank_tier
        FROM stage_events se
        INNER JOIN transactions t ON se.transaction_id = t.transaction_id
        LEFT JOIN devices d ON t.device_id = d.device_id
        LEFT JOIN funnel_stages fs ON se.stage_id = fs.stage_id
        LEFT JOIN error_codes ec ON se.error_code = ec.error_code
        LEFT JOIN banks pb ON t.payer_bank_id = pb.bank_id
        LEFT JOIN banks pyb ON t.payee_bank_id = pyb.bank_id
    """

    with engine.begin() as conn:
        result = conn.execute(text(insert_query))
        row_count = result.rowcount

    logging.info("Inserted %s rows into transaction_summary", row_count)
    return row_count


def build_transaction_summary(engine):
    recreate_transaction_summary_table(engine)
    return populate_transaction_summary(engine)


def run_cleaning_pipeline():
    start = time.time()
    engine = get_engine()

    logging.info("Started cleaning pipeline and transaction_summary build.")
    clean_database_tables(engine)
    summary_rows = build_transaction_summary(engine)

    total_time = (time.time() - start) / 60
    logging.info(
        "Completed cleaning pipeline in %.2f minutes. transaction_summary rows: %s",
        total_time,
        summary_rows,
    )
    print(f"transaction_summary created with {summary_rows} rows.")


if __name__ == "__main__":
    run_cleaning_pipeline()
