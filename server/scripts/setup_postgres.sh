#!/bin/bash
# PostgreSQL Setup Script for MooPredict
# Run this on your Mac to set up shared database

set -e

echo "🐘 Setting up PostgreSQL for MooPredict..."

# Check if PostgreSQL is installed
if ! command -v psql &> /dev/null; then
    echo "📦 Installing PostgreSQL..."
    if command -v brew &> /dev/null; then
        brew install postgresql@14
        brew services start postgresql@14
    else
        echo "❌ Please install Homebrew first: https://brew.sh"
        exit 1
    fi
fi

echo "✅ PostgreSQL is installed"

# Wait for PostgreSQL to start
sleep 2

# Create database and user
DB_NAME="moopredict"
DB_USER="moopredict_user"
DB_PASS="moopredict_secure_pass_2024"

echo "🗄️ Creating database..."

# Create user
psql postgres -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || echo "User already exists"

# Create database
psql postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || echo "Database already exists"

# Grant permissions
psql postgres -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

echo "✅ Database created!"

# Get Mac's IP address for OpenClaw to connect
IP_ADDRESS=$(ipconfig getifaddr en0 || ipconfig getifaddr en1 || echo "localhost")

echo ""
echo "📝 Connection Details:"
echo "  Host: $IP_ADDRESS"
echo "  Port: 5432"
echo "  Database: $DB_NAME"
echo "  User: $DB_USER"
echo "  Password: $DB_PASS"
echo ""
echo "🔗 Connection String:"
echo "  postgresql://$DB_USER:$DB_PASS@$IP_ADDRESS:5432/$DB_NAME"
echo ""

# Save connection string to .env
cat > .env.db << EOF
# PostgreSQL Configuration
DATABASE_URL=postgresql://$DB_USER:$DB_PASS@$IP_ADDRESS:5432/$DB_NAME
DB_HOST=$IP_ADDRESS
DB_PORT=5432
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASS=$DB_PASS
EOF

echo "💾 Connection details saved to .env.db"
echo ""
echo "⚠️  IMPORTANT: Add this to your .env file:"
echo "   DATABASE_URL=postgresql://$DB_USER:$DB_PASS@$IP_ADDRESS:5432/$DB_NAME"
echo ""
echo "🔒 Security Note: Make sure your firewall allows port 5432"
echo "   sudo ufw allow 5432/tcp  # If using UFW"
echo ""
echo "🚀 Next step: Run the migration script!"
echo "   npm run migrate:pg"
