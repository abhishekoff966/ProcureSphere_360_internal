import sys, os
sys.path.insert(0, 'src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
import django
django.setup()
from django.db import connection
from django.apps import apps

django_tables = {model._meta.db_table for model in apps.get_models()}

with connection.cursor() as cursor:
    cursor.execute('''
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        ORDER BY table_name;
    ''')
    tables = [row[0] for row in cursor.fetchall()]
    
    print('Total public tables in DB:', len(tables))
    print('Total Django registered model tables:', len(django_tables))
    
    orphan_tables = []
    for t in tables:
        if t not in django_tables and not t.startswith('django_') and not t.startswith('auth_') and not t.startswith('axes_'):
            orphan_tables.append(t)
            
    print(f'Orphan tables in DB not mapped to any active Django model ({len(orphan_tables)}):')
    for ot in orphan_tables:
        cursor.execute(f'SELECT count(*) FROM "{ot}";')
        cnt = cursor.fetchone()[0]
        print(f'  {ot:35} (row count: {cnt})')

