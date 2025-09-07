import os


class Config:
    # --- Core App Config ---
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-super-secret-key-you-should-change'
    
    # --- MongoDB Atlas Configuration ---
    # Replace the placeholder with your actual Atlas connection string
    # It's highly recommended to set this as an environment variable in production
    MONGO_URI = (
    os.environ.get('MONGO_URI') or
    "mongodb+srv://harshdeep_db_user:5aBM8pWh5nXgqOZ4@sih.zkbv6yk.mongodb.sih_flask/?retryWrites=true&w=majority&appName=Sih"
)


    # --- QR Token Config ---
    QR_REFRESH_RATE_SECONDS = 2
    TOKEN_VALIDITY_SECONDS = 5