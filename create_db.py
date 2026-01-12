import os
import pymysql
import dotenv

dotenv.load_dotenv()

conn = pymysql.connect(
    host = os.getenv('DB_HOST'),
    user = os.getenv('USER'),
    password = os.getenv('PASSWORD')
                     
)

cursor = conn.cursor()
cursor.execute(f"""CREATE DATABASE IF NOT EXISTS {os.getenv('DB_NAME')}""")

print("Database created successfully!")
               
conn.close()






