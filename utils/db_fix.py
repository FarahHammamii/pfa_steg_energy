"""
Fix for database connection with special characters
Create this as: utils/db_fix.py
"""

import urllib.parse
import os
from dotenv import load_dotenv

load_dotenv()

def encode_db_url():
    """Properly encode database URL with special characters"""
    db_url = os.getenv("NEON_DATABASE_URL")
    
    if not db_url:
        print("❌ NEON_DATABASE_URL not found in environment")
        return None
    
    # Parse the URL
    # Format: postgresql://username:password@host:port/database
    
    try:
        # Split into parts
        protocol, rest = db_url.split("://", 1)
        auth_host, database = rest.split("/", 1)
        auth, host_port = auth_host.split("@", 1)
        
        if ":" in auth:
            username, password = auth.split(":", 1)
            # URL encode the password
            encoded_password = urllib.parse.quote_plus(password)
            encoded_url = f"{protocol}://{username}:{encoded_password}@{host_port}/{database}"
            
            print(f"Original URL: {db_url.replace(password, '***')}")
            print(f"Encoded URL: {encoded_url.replace(encoded_password, '***')}")
            
            return encoded_url
        else:
            # No password in URL
            return db_url
            
    except Exception as e:
        print(f"❌ Error parsing URL: {e}")
        return db_url


if __name__ == "__main__":
    encoded = encode_db_url()
    if encoded:
        print("\nAdd this to your .env file:")
        print(f"NEON_DATABASE_URL={encoded}")