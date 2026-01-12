import os
import pandas as pd
import logging
from sqlalchemy import create_engine
import time
import dotenv

dotenv.load_dotenv()

logging.basicConfig(
    filename='logs/ingest.log',
    level=logging.INFO,
    format = '%(asctime)s - %(levelname)s - %(message)s',
    filemode = 'a'
)

logging.info('Started the pipeline for ingesting the raw data into MySql database.')

engine = create_engine(f"mysql+pymysql://{os.getenv('USER')}:{os.getenv('PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}")

def ingest_data_to_mysql(df, table_name, engine):
    """This function ingests the raw data into the MySQL database."""
    logging.info(f'Ingesting data into MySql database table: {table_name}')
    try:
        df.to_sql(name = table_name, con = engine, if_exists = 'replace', index = False)
    except Exception as e:
        logging.error(f'Error : {e}')
        print(e)

def load_raw_data():
    """This function loads the raw data from the CSV file into a pandas DataFrame."""
    start = time.time()
    for file in os.listdir('data'):
        if file.endswith('.csv'):
            df = pd.read_csv(f"data/{file}")
            ingest_data_to_mysql(df, file[:-4], engine)
            
    end = time.time()
    total_time = (end - start)/60
    logging.info(f'Total time taken to ingest the raw data into MySql database: {total_time} minutes.')

    logging.info('Completed the pipeline for ingesting the raw data into MySql database.')


if __name__ == "__main__":
    load_raw_data()