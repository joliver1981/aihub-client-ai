-- Migration 005: Add notification_email column to AgentEmailAddresses
-- This enables the "Notify on new emails" and "Notify on auto-replies" features
-- by providing a recipient email address for notifications.

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'AgentEmailAddresses' AND COLUMN_NAME = 'notification_email'
)
BEGIN
    ALTER TABLE AgentEmailAddresses ADD notification_email NVARCHAR(255) NULL;
    PRINT 'Added notification_email column to AgentEmailAddresses';
END
ELSE
BEGIN
    PRINT 'notification_email column already exists';
END
