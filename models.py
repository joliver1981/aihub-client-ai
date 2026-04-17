from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

class User(db.Model, UserMixin):
    __tablename__ = 'User'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column('user_name', db.String(150), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150))
    phone = db.Column(db.String(50))
    role = db.Column(db.Integer, nullable=False)
    password = db.Column(db.String(255))
    # Enterprise identity fields
    auth_provider = db.Column(db.String(50), nullable=False, default='local')
    external_id = db.Column(db.String(255), nullable=True)
    external_email = db.Column(db.String(255), nullable=True)
    last_sso_login = db.Column(db.DateTime, nullable=True)
    mfa_enabled = db.Column(db.Boolean, nullable=False, default=False)
    mfa_secret = db.Column(db.String(255), nullable=True)
    mfa_backup_codes = db.Column(db.Text, nullable=True)
    mfa_enrolled_at = db.Column(db.DateTime, nullable=True)
