import argparse
import getpass
import sys

from email_validator import EmailNotValidError, validate_email
from psycopg.errors import UniqueViolation

from database import connect
from security import hash_password


def create_admin(connection, email: str, name: str, password: str):
    normalized_email = validate_email(email.strip(), check_deliverability=False).normalized.lower()
    if not name.strip() or len(name.strip()) > 120:
        raise ValueError("Name must contain between 1 and 120 characters.")
    password_hash = hash_password(password)
    return connection.execute(
        """INSERT INTO admin (email, name, role, password_hash)
           VALUES (%s, %s, 'super_admin', %s) RETURNING id""",
        (normalized_email, name.strip(), password_hash),
    ).fetchone()["id"]


def main():
    parser = argparse.ArgumentParser(description="Create a Mikan super admin with a hashed password.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("Run in an interactive terminal so the password can be entered privately.")
    try:
        password = getpass.getpass("Password (12-128 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise ValueError("Passwords do not match.")
        with connect() as connection:
            admin_id = create_admin(connection, args.email, args.name, password)
        print(f"Super admin created (ID {admin_id}).")
    except UniqueViolation:
        parser.exit(1, "An admin with this email already exists. No account was changed.\n")
    except (EmailNotValidError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    except (KeyboardInterrupt, EOFError):
        parser.exit(1, "\nCancelled.\n")


if __name__ == "__main__":
    main()