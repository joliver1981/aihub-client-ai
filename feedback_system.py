# feedback_system.py

import csv
import io
import logging
import json
import datetime
from flask import request, jsonify, Response
from flask_login import login_required
from config_db_client import ConfigDatabaseClient
from role_decorators import developer_required


class FeedbackManager:
    """
    Manages collecting, storing, and analyzing user feedback on AI responses.
    """

    def __init__(self, logger=None):
        """
        Initialize the feedback manager.

        Args:
            logger: Logger for recording events (optional)
        """
        self.db = ConfigDatabaseClient()
        self.logger = logger or logging.getLogger(__name__)

    def record_feedback(self, feedback_data):
        """
        Record user feedback in the database.

        Args:
            feedback_data: Dictionary containing feedback information

        Returns:
            dict: Status of the operation
        """
        try:
            required_fields = ['session_id', 'question_id', 'agent_id', 'original_question',
                            'original_answer', 'feedback_type']

            # Validate required fields
            for field in required_fields:
                if field not in feedback_data:
                    raise ValueError(f"Missing required field: {field}")

            # Prepare data for insertion
            insert_sql = """
            INSERT INTO [dbo].[ai_feedback]
                ([session_id], [question_id], [user_id], [agent_id],
                 [original_question], [original_answer], [feedback_type],
                 [feedback_details], [rating], [confidence_score], [caution_level],
                 [sql_query])
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            params = (
                feedback_data['session_id'],
                feedback_data['question_id'],
                feedback_data.get('user_id'),
                feedback_data['agent_id'],
                feedback_data['original_question'],
                feedback_data['original_answer'],
                feedback_data['feedback_type'],
                feedback_data.get('feedback_details'),
                feedback_data.get('rating'),
                feedback_data.get('confidence_score'),
                feedback_data.get('caution_level'),
                feedback_data.get('sql_query')
            )

            self.db.execute_query(insert_sql, params)
            self.logger.info(f"Feedback recorded for question_id: {feedback_data['question_id']}")

            return {"status": "success", "message": "Feedback recorded successfully"}

        except Exception as e:
            self.logger.error(f"Error recording feedback: {e}")
            return {"status": "error", "message": str(e)}

    def get_feedback_summary(self, agent_id=None, time_period=None):
        """
        Get summary statistics of feedback.

        Args:
            agent_id: Optional filter by agent
            time_period: Optional time period filter (e.g. '7d', '30d')

        Returns:
            dict: Feedback statistics
        """
        try:
            # Build the query with optional filters
            query = """
            SELECT
                feedback_type,
                COUNT(*) as count,
                AVG(CAST(rating as FLOAT)) as avg_rating,
                AVG(confidence_score) as avg_confidence
            FROM [dbo].[ai_feedback]
            WHERE 1=1
            """

            params = []

            # Add agent filter if provided
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)

            # Add time period filter if provided
            time_filter = _get_time_period_filter(time_period)
            if time_filter:
                query += time_filter

            # Group by feedback type
            query += " GROUP BY feedback_type"

            # Execute the query
            results = self.db.fetch_query(query, params)

            # Format the results
            summary = {
                "total_feedback": 0,
                "by_type": {},
                "time_period": time_period or "all"
            }

            for row in results:
                feedback_type = row[0]
                count = row[1]
                avg_rating = row[2]
                avg_confidence = row[3]

                summary["total_feedback"] += count
                summary["by_type"][feedback_type] = {
                    "count": count,
                    "avg_rating": avg_rating,
                    "avg_confidence": avg_confidence
                }

            # Get counts by status
            status_query = """
            SELECT [status], COUNT(*) as count
            FROM [dbo].[ai_feedback]
            WHERE 1=1
            """
            status_params = []
            if agent_id:
                status_query += " AND agent_id = ?"
                status_params.append(agent_id)
            if time_filter:
                status_query += time_filter
            status_query += " GROUP BY [status]"

            status_results = self.db.fetch_query(status_query, status_params)
            summary["by_status"] = {}
            for row in status_results:
                summary["by_status"][row[0]] = row[1]

            return summary

        except Exception as e:
            self.logger.error(f"Error getting feedback summary: {e}")
            return {"status": "error", "message": str(e)}

    def get_problematic_questions(self, threshold=3, min_occurrences=2):
        """
        Identify questions that frequently receive negative feedback.

        Args:
            threshold: Rating threshold for negative feedback
            min_occurrences: Minimum occurrences to be considered problematic

        Returns:
            list: Problematic questions with statistics
        """
        try:
            query = """
            SELECT
                original_question,
                COUNT(*) as feedback_count,
                AVG(CAST(rating as FLOAT)) as avg_rating,
                AVG(confidence_score) as avg_confidence
            FROM [dbo].[ai_feedback]
            WHERE rating <= ?
            GROUP BY original_question
            HAVING COUNT(*) >= ?
            ORDER BY COUNT(*) DESC, AVG(CAST(rating as FLOAT)) ASC
            """

            results = self.db.fetch_query(query, (threshold, min_occurrences))

            problematic_questions = []
            for row in results:
                problematic_questions.append({
                    "question": row[0],
                    "feedback_count": row[1],
                    "avg_rating": row[2],
                    "avg_confidence": row[3]
                })

            return problematic_questions

        except Exception as e:
            self.logger.error(f"Error identifying problematic questions: {e}")
            return []


def _get_time_period_filter(time_period):
    """Return a SQL WHERE clause fragment for the given time period."""
    if time_period == '7d':
        return " AND created_at >= DATEADD(day, -7, getutcdate())"
    elif time_period == '30d':
        return " AND created_at >= DATEADD(day, -30, getutcdate())"
    elif time_period == '90d':
        return " AND created_at >= DATEADD(day, -90, getutcdate())"
    return ""


def _build_feedback_filters(args):
    """Build WHERE clause fragments and params from request args for feedback queries."""
    clauses = []
    params = []

    feedback_type = args.get('type', 'all')
    if feedback_type != 'all':
        clauses.append("AND f.feedback_type = ?")
        params.append(feedback_type)

    status = args.get('status', 'all')
    if status != 'all':
        clauses.append("AND f.[status] = ?")
        params.append(status)

    agent_id = args.get('agent_id')
    if agent_id:
        clauses.append("AND f.agent_id = ?")
        params.append(int(agent_id))

    search_term = args.get('search', '')
    if search_term:
        clauses.append("AND (f.original_question LIKE ? OR f.original_answer LIKE ? OR f.feedback_details LIKE ?)")
        search_param = f'%{search_term}%'
        params.extend([search_param, search_param, search_param])

    time_period = args.get('time_period')
    time_filter = _get_time_period_filter(time_period)
    if time_filter:
        clauses.append(time_filter)

    return " ".join(clauses), params


# Flask route handlers for feedback

def setup_feedback_routes(app, feedback_manager):
    """Set up Flask routes for the feedback system."""

    @app.route('/api/feedback', methods=['POST'])
    @login_required
    def submit_feedback():
        """API endpoint for submitting feedback."""
        try:
            feedback_data = request.get_json()
            result = feedback_manager.record_feedback(feedback_data)
            return jsonify(result)
        except Exception as e:
            logging.error(f"Error in feedback submission: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/api/feedback/summary', methods=['GET'])
    @developer_required(api=True)
    def get_feedback_summary():
        """API endpoint for getting feedback summary."""
        try:
            agent_id = request.args.get('agent_id')
            time_period = request.args.get('time_period')

            # Convert agent_id to int if it's provided
            if agent_id and agent_id.isdigit():
                agent_id = int(agent_id)

            summary = feedback_manager.get_feedback_summary(agent_id, time_period)
            return jsonify(summary)
        except Exception as e:
            logging.error(f"Error getting feedback summary: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/api/feedback/problems', methods=['GET'])
    @developer_required(api=True)
    def get_problematic_questions():
        """API endpoint for identifying questions that frequently receive negative feedback."""
        try:
            threshold = int(request.args.get('threshold', 5))
            min_occurrences = int(request.args.get('min_occurrences', 1))

            query = """
            SELECT
                original_question,
                COUNT(*) as feedback_count,
                AVG(CAST(rating as FLOAT)) as avg_rating,
                AVG(confidence_score) as avg_confidence
            FROM [dbo].[ai_feedback]
            WHERE rating <= ?
            GROUP BY original_question
            HAVING COUNT(*) >= ?
            ORDER BY COUNT(*) DESC, AVG(CAST(rating as FLOAT)) ASC
            """

            db = ConfigDatabaseClient()
            results = db.fetch_query(query, [threshold, min_occurrences])

            problematic_questions = []
            for row in results:
                problematic_questions.append({
                    "question": row[0],
                    "feedback_count": row[1],
                    "avg_rating": row[2],
                    "avg_confidence": row[3]
                })

            return jsonify({
                "problematic_questions": problematic_questions
            })

        except Exception as e:
            logging.error(f"Error in get_problematic_questions: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500

    @app.route('/api/feedback', methods=['GET'])
    @developer_required(api=True)
    def get_recent_feedback():
        """API endpoint for getting recent feedback with optional filtering."""
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 10))

            # Build filters
            filter_clause, params = _build_feedback_filters(request.args)

            # Build the query
            query = f"""
            SELECT f.id, f.session_id, f.question_id, f.user_id, f.agent_id, a.description as agent_name,
                   f.original_question, f.original_answer, f.feedback_type,
                   f.feedback_details, f.rating, f.confidence_score, f.caution_level, f.created_at,
                   f.sql_query, f.[status], u.name as username
            FROM [dbo].[ai_feedback] f
            LEFT JOIN [dbo].[Agents] a ON f.agent_id = a.id
            LEFT JOIN [dbo].[User] u ON f.user_id = u.id
            WHERE 1=1 {filter_clause}
            ORDER BY f.created_at DESC
            OFFSET {(page - 1) * per_page} ROWS FETCH NEXT {per_page} ROWS ONLY
            """

            db = ConfigDatabaseClient()
            results = db.fetch_query(query, params)

            feedback_data = []
            for row in results:
                feedback_data.append({
                    'id': row[0],
                    'session_id': row[1],
                    'question_id': row[2],
                    'user_id': row[3],
                    'agent_id': row[4],
                    'agent_name': row[5],
                    'original_question': row[6],
                    'original_answer': row[7],
                    'feedback_type': row[8],
                    'feedback_details': row[9],
                    'rating': row[10],
                    'confidence_score': row[11],
                    'caution_level': row[12],
                    'created_at': row[13].isoformat() if row[13] else None,
                    'sql_query': row[14],
                    'status': row[15],
                    'username': row[16]
                })

            # Get total count for pagination
            count_query = f"""
            SELECT COUNT(*)
            FROM [dbo].[ai_feedback] f
            WHERE 1=1 {filter_clause}
            """

            count_result = db.fetch_one(count_query, params)
            total_count = count_result[0] if count_result else 0

            return jsonify({
                'status': 'success',
                'feedback': feedback_data,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total_count': total_count,
                    'total_pages': (total_count + per_page - 1) // per_page
                }
            })

        except Exception as e:
            logging.error(f"Error in get_recent_feedback: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500


    @app.route('/api/feedback/detail', methods=['GET'])
    @developer_required(api=True)
    def get_feedback_detail():
        """API endpoint for getting details of a specific feedback entry."""
        try:
            feedback_id = request.args.get('id')

            if not feedback_id:
                return jsonify({'status': 'error', 'message': 'Missing feedback ID'}), 400

            query = """
            SELECT f.id, f.session_id, f.question_id, f.user_id, f.agent_id, a.description as agent_name,
                   f.original_question, f.original_answer, f.feedback_type,
                   f.feedback_details, f.rating, f.confidence_score, f.caution_level, f.created_at,
                   f.sql_query, f.[status], f.admin_notes, f.reviewed_by, f.reviewed_at,
                   u.name as username, r.name as reviewer_name
            FROM [dbo].[ai_feedback] f
            LEFT JOIN [dbo].[Agents] a ON f.agent_id = a.id
            LEFT JOIN [dbo].[User] u ON f.user_id = u.id
            LEFT JOIN [dbo].[User] r ON f.reviewed_by = r.id
            WHERE f.id = ?
            """

            db = ConfigDatabaseClient()
            result = db.fetch_one(query, [feedback_id])

            if not result:
                return jsonify({'status': 'error', 'message': 'Feedback not found'}), 404

            feedback_data = {
                'id': result[0],
                'session_id': result[1],
                'question_id': result[2],
                'user_id': result[3],
                'agent_id': result[4],
                'agent_name': result[5],
                'original_question': result[6],
                'original_answer': result[7],
                'feedback_type': result[8],
                'feedback_details': result[9],
                'rating': result[10],
                'confidence_score': result[11],
                'caution_level': result[12],
                'created_at': result[13].isoformat() if result[13] else None,
                'sql_query': result[14],
                'status': result[15],
                'admin_notes': result[16],
                'reviewed_by': result[17],
                'reviewed_at': result[18].isoformat() if result[18] else None,
                'username': result[19],
                'reviewer_name': result[20]
            }

            return jsonify(feedback_data)

        except Exception as e:
            logging.error(f"Error in get_feedback_detail: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500


    @app.route('/api/feedback/status', methods=['PUT'])
    @developer_required(api=True)
    def update_feedback_status():
        """API endpoint for updating feedback status and admin notes (developer/admin only)."""
        try:
            data = request.get_json()
            feedback_id = data.get('id')
            new_status = data.get('status')
            admin_notes = data.get('admin_notes')
            reviewed_by = data.get('reviewed_by')

            if not feedback_id or not new_status:
                return jsonify({'status': 'error', 'message': 'Missing required fields (id, status)'}), 400

            if new_status not in ('new', 'reviewed', 'resolved'):
                return jsonify({'status': 'error', 'message': 'Invalid status. Must be: new, reviewed, or resolved'}), 400

            query = """
            UPDATE [dbo].[ai_feedback]
            SET [status] = ?, [admin_notes] = ?, [reviewed_by] = ?, [reviewed_at] = GETUTCDATE()
            WHERE id = ?
            """
            db = ConfigDatabaseClient()
            db.execute_query(query, [new_status, admin_notes, reviewed_by, feedback_id])

            return jsonify({'status': 'success', 'message': 'Feedback updated successfully'})
        except Exception as e:
            logging.error(f"Error updating feedback status: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


    @app.route('/api/feedback/export', methods=['GET'])
    @developer_required(api=True)
    def export_feedback():
        """API endpoint for exporting feedback data as CSV (developer/admin only)."""
        try:
            filter_clause, params = _build_feedback_filters(request.args)

            query = f"""
            SELECT f.id, f.created_at, a.description as agent_name,
                   u.name as username, f.original_question, f.original_answer,
                   f.sql_query, f.feedback_type, f.feedback_details, f.rating,
                   f.confidence_score, f.caution_level, f.[status], f.admin_notes
            FROM [dbo].[ai_feedback] f
            LEFT JOIN [dbo].[Agents] a ON f.agent_id = a.id
            LEFT JOIN [dbo].[User] u ON f.user_id = u.id
            WHERE 1=1 {filter_clause}
            ORDER BY f.created_at DESC
            """

            db = ConfigDatabaseClient()
            results = db.fetch_query(query, params)

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['ID', 'Date', 'Agent', 'User', 'Question', 'Answer',
                            'SQL Query', 'Type', 'Details', 'Rating',
                            'Confidence', 'Caution', 'Status', 'Admin Notes'])
            for row in results:
                writer.writerow(row)

            return Response(
                output.getvalue(),
                mimetype='text/csv',
                headers={'Content-Disposition': 'attachment; filename=feedback_export.csv'}
            )
        except Exception as e:
            logging.error(f"Error exporting feedback: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


    @app.route('/api/feedback/by-agent', methods=['GET'])
    @developer_required(api=True)
    def get_feedback_by_agent():
        """API endpoint for getting feedback data grouped by agent."""
        try:
            time_period = request.args.get('time_period', 'all')

            query = """
            SELECT a.id, a.description,
                   SUM(CASE WHEN f.feedback_type = 'positive' THEN 1 ELSE 0 END) as positive_count,
                   SUM(CASE WHEN f.feedback_type = 'negative' THEN 1 ELSE 0 END) as negative_count,
                   SUM(CASE WHEN f.[feedback_details] IS NOT NULL THEN 1 ELSE 0 END) as detailed_count,
                   AVG(CAST(f.rating as FLOAT)) as avg_rating,
                   AVG(f.confidence_score) as avg_confidence
            FROM [dbo].[Agents] a
            LEFT JOIN [dbo].[ai_feedback] f ON a.id = f.agent_id
            WHERE a.is_data_agent = 1
            """

            params = []
            time_filter = _get_time_period_filter(time_period)
            if time_filter:
                query += time_filter

            query += " GROUP BY a.id, a.description"

            db = ConfigDatabaseClient()
            results = db.fetch_query(query, params)

            agent_data = []
            for row in results:
                agent_data.append({
                    'agent_id': row[0],
                    'agent_name': row[1],
                    'positive_count': row[2],
                    'negative_count': row[3],
                    'detailed_count': row[4],
                    'avg_rating': row[5] if row[5] is not None else 0,
                    'avg_confidence': row[6] if row[6] is not None else 0
                })

            return jsonify(agent_data)

        except Exception as e:
            logging.error(f"Error in get_feedback_by_agent: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500


    @app.route('/api/feedback/trends', methods=['GET'])
    @developer_required(api=True)
    def get_feedback_trends():
        """API endpoint for getting feedback trends over time."""
        try:
            time_period = request.args.get('time_period', '7d')

            # Determine the date format and grouping
            date_format = '%Y-%m-%d'
            date_trunc = 'DAY'

            if time_period == '7d':
                date_trunc = 'DAY'
                date_format = '%a'
            elif time_period == '30d':
                date_trunc = 'WEEK'
                date_format = 'Week %W'
            elif time_period == '90d':
                date_trunc = 'MONTH'
                date_format = '%b'
            else:
                date_trunc = 'QUARTER'
                date_format = 'Q%Q %Y'

            query = """
            SELECT
                FORMAT(DATETRUNC({0}, created_at), '{1}') as date_label,
                COUNT(*) as count,
                AVG(CAST(rating as FLOAT)) as avg_rating,
                AVG(confidence_score) as avg_confidence
            FROM [dbo].[ai_feedback]
            WHERE 1=1
            """.format(date_trunc, date_format)

            params = []
            time_filter = _get_time_period_filter(time_period)
            if time_filter:
                query += time_filter

            query += " GROUP BY DATETRUNC({0}, created_at)".format(date_trunc)
            query += " ORDER BY DATETRUNC({0}, created_at)".format(date_trunc)

            db = ConfigDatabaseClient()

            try:
                results = db.fetch_query(query, params)
            except:
                # Fallback query for older SQL Server versions without DATETRUNC
                if time_period == '7d':
                    fallback_query = """
                    SELECT
                        FORMAT(created_at, 'ddd') as date_label,
                        COUNT(*) as count,
                        AVG(CAST(rating as FLOAT)) as avg_rating,
                        AVG(confidence_score) as avg_confidence
                    FROM [dbo].[ai_feedback]
                    WHERE created_at >= DATEADD(day, -7, getutcdate())
                    GROUP BY FORMAT(created_at, 'ddd')
                    ORDER BY MIN(created_at)
                    """
                elif time_period == '30d':
                    fallback_query = """
                    SELECT
                        'Week ' + CAST(DATEPART(week, created_at) AS VARCHAR) as date_label,
                        COUNT(*) as count,
                        AVG(CAST(rating as FLOAT)) as avg_rating,
                        AVG(confidence_score) as avg_confidence
                    FROM [dbo].[ai_feedback]
                    WHERE created_at >= DATEADD(day, -30, getutcdate())
                    GROUP BY DATEPART(week, created_at)
                    ORDER BY DATEPART(week, created_at)
                    """
                elif time_period == '90d':
                    fallback_query = """
                    SELECT
                        FORMAT(created_at, 'MMM') as date_label,
                        COUNT(*) as count,
                        AVG(CAST(rating as FLOAT)) as avg_rating,
                        AVG(confidence_score) as avg_confidence
                    FROM [dbo].[ai_feedback]
                    WHERE created_at >= DATEADD(day, -90, getutcdate())
                    GROUP BY FORMAT(created_at, 'MMM'), MONTH(created_at)
                    ORDER BY MONTH(created_at)
                    """
                else:
                    fallback_query = """
                    SELECT
                        'Q' + CAST(DATEPART(quarter, created_at) AS VARCHAR) + ' ' +
                        CAST(YEAR(created_at) AS VARCHAR) as date_label,
                        COUNT(*) as count,
                        AVG(CAST(rating as FLOAT)) as avg_rating,
                        AVG(confidence_score) as avg_confidence
                    FROM [dbo].[ai_feedback]
                    GROUP BY DATEPART(quarter, created_at), YEAR(created_at)
                    ORDER BY YEAR(created_at), DATEPART(quarter, created_at)
                    """

                results = db.fetch_query(fallback_query, [])

            trend_data = []
            for row in results:
                trend_data.append({
                    'date_label': row[0],
                    'count': row[1],
                    'avg_rating': row[2] if row[2] is not None else 0,
                    'avg_confidence': row[3] if row[3] is not None else 0
                })

            return jsonify(trend_data)

        except Exception as e:
            logging.error(f"Error in get_feedback_trends: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
