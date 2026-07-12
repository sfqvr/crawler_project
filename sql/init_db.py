import os
import sys
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        user=os.getenv("POSTGRES_USER", "prefect"),
        password=os.getenv("POSTGRES_PASSWORD", "prefect"),
        database=os.getenv("POSTGRES_DB", "prefect"),
        connect_timeout=10
    )

def run_migration(conn, sql_path: Path):
    print(f"📄 Выполняется миграция: {sql_path.name}")
    
    with open(sql_path, 'r', encoding='utf-8') as f:
        sql = f.read()
    
    with conn.cursor() as cur:
        cur.execute(sql)
    
    conn.commit()
    print(f"✅ Миграция {sql_path.name} выполнена успешно")

def main():
    print("🚀 Инициализация базы данных...")
    conn = None
    
    try:
        conn = get_db_connection()
        print(f"✅ Подключение к PostgreSQL установлено")
        
        migrations_dir = Path(__file__).parent / "migrations"
        migration_files = sorted(migrations_dir.glob("*.sql"))
        
        if not migration_files:
            print("⚠️ Файлы миграций не найдены")
            return
        
        print(f"📂 Найдено {len(migration_files)} миграций")
        
        for migration_file in migration_files:
            run_migration(conn, migration_file)
        
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name IN ('documents', 'repo_sources')
            """)
            tables = cur.fetchall()
            
            print("\n📊 Созданные таблицы:")
            for table in tables:
                print(f"  - {table[0]}")
        
        print("\n✅ Инициализация базы данных завершена успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    main()