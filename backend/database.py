import boto3
import os
import uuid
import hashlib
from datetime import datetime, timedelta
from boto3.dynamodb.conditions import Key

# Magic tokens no expiran. Mantenemos el campo `token_expires_at` con una fecha
# muy lejana para compatibilidad con datos existentes y por si en el futuro se
# reactiva la expiración. La revocación se hace vía status: inactive o
# /admin/clients/:id/regenerate-token.
TOKEN_TTL_DAYS = 365 * 100

# Use environment variables injected by Serverless Framework
CLIENTS_TABLE = os.environ.get('CLIENTS_TABLE', 'shalom-proxy-api-clients-dev')
ADMIN_TABLE = os.environ.get('ADMIN_TABLE', 'shalom-proxy-api-admin-dev')
REGION = os.environ.get('AWS_REGION', 'us-east-1')

dynamodb = boto3.resource('dynamodb', region_name=REGION)

def get_clients_table():
    return dynamodb.Table(CLIENTS_TABLE)

def get_admin_table():
    return dynamodb.Table(ADMIN_TABLE)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def seed_admin():
    table = get_admin_table()
    try:
        response = table.get_item(Key={'username': 'admin'})
        if 'Item' not in response:
            table.put_item(
                Item={
                    'username': 'admin',
                    'password_hash': hash_password('admin123')
                }
            )
    except Exception as e:
        print(f"Error seeding admin: {e}")

# Call seed_admin when the module loads
seed_admin()

def verify_admin(username, password):
    table = get_admin_table()
    try:
        response = table.get_item(Key={'username': username})
        item = response.get('Item')
        if item and item.get('password_hash') == hash_password(password):
            return True
    except Exception as e:
        print(f"Error verifying admin: {e}")
    return False

def create_client(name, email, instance_id, api_key, shalom_username, shalom_password,
                   person_name=None, person_document=None):
    table = get_clients_table()
    client_id = str(uuid.uuid4())
    magic_token = str(uuid.uuid4())
    expires_at = (datetime.now() + timedelta(days=TOKEN_TTL_DAYS)).isoformat()
    created_at = datetime.now().isoformat()
    
    item = {
        'id': client_id,
        'name': name,
        'email': email,
        'shalom_username': shalom_username,
        'shalom_password': shalom_password,
        'magic_token': magic_token,
        'token_expires_at': expires_at,
        'instance_id': instance_id,
        'api_key': api_key,
        'created_at': created_at,
        'status': 'active'
    }
    # Datos del remitente Shalom (obtenidos vía /get-user al momento de crear)
    if person_name:
        item['person_name'] = person_name
    if person_document:
        item['person_document'] = person_document

    table.put_item(Item=item)
    return client_id, magic_token

def get_clients():
    table = get_clients_table()
    try:
        response = table.scan()
        return response.get('Items', [])
    except Exception as e:
        print(f"Error getting clients: {e}")
        return []

def regenerate_magic_token(client_id):
    table = get_clients_table()
    magic_token = str(uuid.uuid4())
    expires_at = (datetime.now() + timedelta(days=TOKEN_TTL_DAYS)).isoformat()
    
    table.update_item(
        Key={'id': client_id},
        UpdateExpression="set magic_token=:m, token_expires_at=:e",
        ExpressionAttributeValues={
            ':m': magic_token,
            ':e': expires_at
        }
    )
    return magic_token

def get_client_by_token(magic_token):
    table = get_clients_table()
    try:
        response = table.query(
            IndexName='MagicTokenIndex',
            KeyConditionExpression=Key('magic_token').eq(magic_token)
        )
        items = response.get('Items', [])
        if items:
            item = items[0]
            if item.get('status') == 'active':
                return item
    except Exception as e:
        print(f"Error getting client by token: {e}")
    return None

def get_client_by_api_key(api_key):
    table = get_clients_table()
    try:
        response = table.query(
            IndexName='ApiKeyIndex',
            KeyConditionExpression=Key('api_key').eq(api_key)
        )
        items = response.get('Items', [])
        if items:
            item = items[0]
            if item.get('status') == 'active':
                return item
    except Exception as e:
        print(f"Error getting client by api_key: {e}")
    return None

def update_client_status(client_id, status):
    table = get_clients_table()
    table.update_item(
        Key={'id': client_id},
        UpdateExpression="set #s=:s",
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': status}
    )

def update_client_credentials(client_id, instance_id, api_key):
    table = get_clients_table()
    table.update_item(
        Key={'id': client_id},
        UpdateExpression="set instance_id=:i, api_key=:a",
        ExpressionAttributeValues={
            ':i': instance_id,
            ':a': api_key
        }
    )

def update_client_person(client_id, person_name=None, person_document=None):
    """Actualiza los datos del remitente (person) de un cliente en DynamoDB."""
    table = get_clients_table()
    updates = []
    values = {}
    if person_name is not None:
        updates.append("person_name=:pn")
        values[':pn'] = person_name
    if person_document is not None:
        updates.append("person_document=:pd")
        values[':pd'] = person_document
    if not updates:
        return
    table.update_item(
        Key={'id': client_id},
        UpdateExpression="set " + ", ".join(updates),
        ExpressionAttributeValues=values
    )

def delete_client(client_id):
    table = get_clients_table()
    table.delete_item(Key={'id': client_id})

def get_client_by_id(client_id):
    table = get_clients_table()
    try:
        response = table.get_item(Key={'id': client_id})
        return response.get('Item')
    except Exception:
        return None

