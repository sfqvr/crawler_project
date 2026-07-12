import os
import json
from contextlib import contextmanager
from typing import Optional, Dict, Any, List
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv

load_dotenv()

class Database:
    def __init__(self):
        self.conn_params = {
            'host': os.getenv("POSTGRES_HOST", "localhost"),
            'port': os.getenv("POSTGRES_PORT", "5432"),
            'user': os.getenv("POSTGRES_USER", "prefect"),
            'password': os.getenv("POSTGRES_PASSWORD", "prefect"),
            'database': os.getenv("POSTGRES_DB", "prefect"),
            'connect_timeout': 10
        }
        self._conn = None
    
    def connect(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(**self.conn_params)
        return self._conn
    
    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None
    
    @contextmanager
    def get_cursor(self, dict_cursor: bool = False):
        conn = self.connect()
        try:
            if dict_cursor:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
            else:
                cursor = conn.cursor()
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
    
    def execute(self, query: str, params: tuple = (), dict_cursor: bool = False):
        with self.get_cursor(dict_cursor) as cur:
            cur.execute(query, params)
            if cur.description:
                return cur.fetchall()
            return None
    
    def execute_one(self, query: str, params: tuple = (), dict_cursor: bool = False):
        with self.get_cursor(dict_cursor) as cur:
            cur.execute(query, params)
            return cur.fetchone()
    
    def create_document(self, url: str, name: str, description: str) -> bool:
        query = """
            INSERT INTO documents (url, name, description, status, discovered_at)
            VALUES (%s, %s, %s, 'new', NOW())
            ON CONFLICT (url) DO NOTHING
        """
        result = self.execute(query, (url, name, description))
        return result is not None
    
    def upsert_document_from_stage1(self, data: Dict[str, Any]) -> None:
        query = """
            INSERT INTO documents (url, name, description, error, status, discovered_at)
            VALUES (%s, %s, %s, %s, 'new', NOW())
            ON CONFLICT (url) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                error = EXCLUDED.error,
                updated_at = NOW()
        """
        self.execute(query, (
            data.get('url'),
            data.get('name'),
            data.get('description'),
            data.get('error', False)
        ))

    def update_stage3_result(self, url: str, data: Dict[str, Any]) -> None:
        query = """
            UPDATE documents SET
                cleaned_html = %s,
                crawl_success = %s,
                crawl_error_message = %s,
                crawl_error_primary = %s,
                crawl_error_fallback = %s,
                crawl_mode = %s,
                crawl_url_type = %s,
                cleaned_html_length = %s,
                crawl_suspect = %s,
                crawl_attempt_count = %s,
                crawl_primary_success = %s,
                crawl_fallback_used = %s,
                crawl_started_at_utc = %s,
                crawl_finished_at_utc = %s,
                crawl_elapsed_seconds = %s,
                crawler_version_hint = %s,
                debug_requested_url = %s,
                debug_result_url = %s,
                debug_status_code = %s,
                debug_match_method = %s,
                debug_batch_id = %s,
                updated_at = NOW(),
                status = CASE 
                    WHEN %s = true THEN 'in_progress' 
                    ELSE 'error' 
                END,
                last_error = CASE 
                    WHEN %s = false THEN %s 
                    ELSE last_error 
                END
            WHERE url = %s
        """
        self.execute(query, (
            data.get('cleaned_html'),
            data.get('crawl_success', False),
            data.get('crawl_error_message', ''),
            data.get('crawl_error_primary', ''),
            data.get('crawl_error_fallback', ''),
            data.get('crawl_mode', ''),
            data.get('crawl_url_type', ''),
            data.get('cleaned_html_length', 0),
            data.get('crawl_suspect', False),
            data.get('crawl_attempt_count', 0),
            data.get('crawl_primary_success', False),
            data.get('crawl_fallback_used', False),
            data.get('crawl_started_at_utc'),
            data.get('crawl_finished_at_utc'),
            data.get('crawl_elapsed_seconds', 0.0),
            data.get('crawler_version_hint', ''),
            data.get('debug_requested_url', ''),
            data.get('debug_result_url', ''),
            data.get('debug_status_code'),
            data.get('debug_match_method', ''),
            data.get('debug_batch_id'),
            data.get('crawl_success', False),  # for status
            data.get('crawl_success', False),  # for last_error
            data.get('crawl_error_message', ''),  # for last_error
            url
        ))
    
    def mark_stage3_completed(self, url: str, success: bool, error_message: str = None) -> None:
        status = 'in_progress' if success else 'error'
        query = """
            UPDATE documents SET
                crawl_success = %s,
                status = %s,
                updated_at = NOW(),
                last_error = CASE 
                    WHEN %s = false THEN %s 
                    ELSE last_error 
                END,
                last_error_at = CASE 
                    WHEN %s = false THEN NOW() 
                    ELSE last_error_at 
                END,
                retry_count = CASE 
                    WHEN %s = false THEN retry_count + 1 
                    ELSE retry_count 
                END
            WHERE url = %s
        """
        self.execute(query, (success, status, success, error_message, success, success, url))
    
    def update_stage4_result(self, url: str, data: Dict[str, Any]) -> None:
        stage4 = data.get('stage4', {})
        assessment = stage4.get('assessment', {})
        
        query = """
            UPDATE documents SET
                -- Stage4 metadata
                stage4_success = %s,
                stage4_error_message = %s,
                stage4_model_name = %s,
                stage4_prompt_version = %s,
                stage4_processed_at_utc = %s,
                stage4_input_html_length = %s,
                stage4_llm_input_html_length = %s,
                stage4_html_was_truncated = %s,
                stage4_agent_final_message = %s,
                
                -- Assessment
                is_relevant = %s,
                can_extract_markdown = %s,
                relevance_reason = %s,
                document_kind = %s,
                contains_main_text = %s,
                contains_incident_information = %s,
                has_timeline = %s,
                has_root_cause = %s,
                has_impact_description = %s,
                has_resolution_or_mitigation = %s,
                has_action_items_or_lessons_learned = %s,
                language = %s,
                relevance_confidence = %s,
                
                updated_at = NOW(),
                status = CASE 
                    WHEN %s = true AND %s = true THEN 'in_progress'
                    WHEN %s = true AND %s = false THEN 'skipped'
                    ELSE 'error'
                END,
                last_error = CASE 
                    WHEN %s = false THEN %s 
                    ELSE last_error 
                END
            WHERE url = %s
        """
        self.execute(query, (
            stage4.get('success', False),
            stage4.get('error_message', ''),
            stage4.get('model_name', ''),
            stage4.get('prompt_version', ''),
            stage4.get('processed_at_utc'),
            stage4.get('input_html_length', 0),
            stage4.get('llm_input_html_length', 0),
            stage4.get('html_was_truncated', False),
            stage4.get('agent_final_message', ''),
            
            assessment.get('is_relevant', False),
            assessment.get('can_extract_markdown', False),
            assessment.get('reason', ''),
            assessment.get('document_kind', ''),
            assessment.get('contains_main_text', False),
            assessment.get('contains_incident_information', False),
            assessment.get('has_timeline', False),
            assessment.get('has_root_cause', False),
            assessment.get('has_impact_description', False),
            assessment.get('has_resolution_or_mitigation', False),
            assessment.get('has_action_items_or_lessons_learned', False),
            assessment.get('language'),
            assessment.get('confidence', 0.0),
            
            stage4.get('success', False),
            assessment.get('is_relevant', False),
            stage4.get('success', False),
            assessment.get('is_relevant', False),
            stage4.get('success', False),
            stage4.get('error_message', ''),
            url
        ))

    def update_stage5_result(self, url: str, data: Dict[str, Any]) -> None:
        stage5 = data.get('stage5', {})
        
        query = """
            UPDATE documents SET
                stage5_success = %s,
                stage5_error_message = %s,
                stage5_model_name = %s,
                stage5_prompt_version = %s,
                stage5_processed_at_utc = %s,
                stage5_input_html_length = %s,
                stage5_llm_input_html_length = %s,
                stage5_html_was_truncated = %s,
                stage5_agent_final_message = %s,
                markdown_length = %s,
                markdown_content = %s,
                updated_at = NOW(),
                status = CASE 
                    WHEN %s = true THEN 'in_progress'
                    ELSE 'error'
                END,
                last_error = CASE 
                    WHEN %s = false THEN %s 
                    ELSE last_error 
                END
            WHERE url = %s
        """
        self.execute(query, (
            stage5.get('success', False),
            stage5.get('error_message', ''),
            stage5.get('model_name', ''),
            stage5.get('prompt_version', ''),
            stage5.get('processed_at_utc'),
            stage5.get('input_html_length', 0),
            stage5.get('llm_input_html_length', 0),
            stage5.get('html_was_truncated', False),
            stage5.get('agent_final_message', ''),
            stage5.get('markdown_length', 0),
            stage5.get('markdown_content', ''),
            stage5.get('success', False),
            stage5.get('success', False),
            stage5.get('error_message', ''),
            url
        ))
 
    def update_stage6_result(self, url: str, data: Dict[str, Any]) -> None:
        stage6 = data.get('stage6', {})
        extraction = stage6.get('extraction', {})
        metadata_filters = extraction.get('metadata_filters', {})
        searchable_text = extraction.get('searchable_text', {})
        
        query = """
            UPDATE documents SET
                -- Stage6 metadata
                stage6_success = %s,
                stage6_error_message = %s,
                stage6_model_name = %s,
                stage6_prompt_version = %s,
                stage6_processed_at_utc = %s,
                stage6_input_markdown_length = %s,
                stage6_llm_input_markdown_length = %s,
                stage6_markdown_was_truncated = %s,
                stage6_agent_final_message = %s,
                
                -- Extraction
                company = %s,
                incident_date = %s,
                short_description = %s,
                incident_categories = %s,
                tech_stack = %s,
                infrastructure = %s,
                key_terms = %s,
                symptoms = %s,
                root_cause = %s,
                resolution = %s,
                lessons_learned = %s,
                metadata_confidence = %s,
                
                updated_at = NOW(),
                status = CASE 
                    WHEN %s = true THEN 'in_progress'
                    ELSE 'error'
                END,
                last_error = CASE 
                    WHEN %s = false THEN %s 
                    ELSE last_error 
                END
            WHERE url = %s
        """
        self.execute(query, (
            stage6.get('success', False),
            stage6.get('error_message', ''),
            stage6.get('model_name', ''),
            stage6.get('prompt_version', ''),
            stage6.get('processed_at_utc'),
            stage6.get('input_markdown_length', 0),
            stage6.get('llm_input_markdown_length', 0),
            stage6.get('markdown_was_truncated', False),
            stage6.get('agent_final_message', ''),
            
            extraction.get('company'),
            extraction.get('date'),
            extraction.get('short_description', ''),
            Json(metadata_filters.get('incident_categories', [])),
            Json(metadata_filters.get('tech_stack', [])),
            Json(metadata_filters.get('infrastructure', [])),
            Json(metadata_filters.get('key_terms', [])),
            searchable_text.get('symptoms', ''),
            searchable_text.get('root_cause', ''),
            searchable_text.get('resolution', ''),
            searchable_text.get('lessons_learned', ''),
            extraction.get('confidence', 0.0),
            
            stage6.get('success', False),
            stage6.get('success', False),
            stage6.get('error_message', ''),
            url
        ))
    
    def update_stage7_result(self, url: str, data: Dict[str, Any]) -> None:
        """
        Сохраняет результаты Stage 7 (подготовка для Qdrant) в БД.
        """
        query = """
            UPDATE documents SET
                qdrant_point_id = %s,
                tech_stack_text = %s,
                key_terms_text = %s,
                embedding_text = %s,
                updated_at = NOW(),
                status = 'in_progress'
            WHERE url = %s
        """
        self.execute(query, (
            data.get('qdrant_point_id'),
            data.get('tech_stack_text', ''),
            data.get('key_terms_text', ''),
            data.get('embedding_text', ''),
            url
        ))
    
    def mark_stage8_completed(self, url: str, success: bool, error_message: str = None) -> None:
        status = 'success' if success else 'error'
        query = """
            UPDATE documents SET
                status = %s,
                processed_at = CASE 
                    WHEN %s = true THEN NOW() 
                    ELSE processed_at 
                END,
                updated_at = NOW(),
                last_error = CASE 
                    WHEN %s = false THEN %s 
                    ELSE last_error 
                END,
                last_error_at = CASE 
                    WHEN %s = false THEN NOW() 
                    ELSE last_error_at 
                END,
                retry_count = CASE 
                    WHEN %s = false THEN retry_count + 1 
                    ELSE retry_count 
                END
            WHERE url = %s
        """
        self.execute(query, (status, success, success, error_message, success, success, url))
    
    def get_document_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM documents WHERE url = %s"
        result = self.execute(query, (url,), dict_cursor=True)
        return dict(result[0]) if result else None
    
    def get_documents_by_status(self, status: str) -> List[Dict[str, Any]]:
        query = "SELECT * FROM documents WHERE status = %s ORDER BY discovered_at"
        result = self.execute(query, (status,), dict_cursor=True)
        return [dict(row) for row in result]
    
    def get_processed_urls(self) -> set:
        query = "SELECT url FROM documents WHERE status = 'success'"
        result = self.execute(query)
        return {row[0] for row in result} if result else set()
    
    def get_urls_needing_stage(self, stage: int) -> List[str]:
        stage_conditions = {
            3: "status = 'new' OR (status = 'error' AND crawl_success IS NULL)",
            4: "status = 'in_progress' AND crawl_success = true AND stage4_success IS NULL",
            5: "status = 'in_progress' AND stage4_success = true AND is_relevant = true AND stage5_success IS NULL",
            6: "status = 'in_progress' AND stage5_success = true AND stage6_success IS NULL",
            7: "status = 'in_progress' AND stage6_success = true AND qdrant_point_id IS NULL",
            8: "status = 'in_progress' AND qdrant_point_id IS NOT NULL AND status != 'success'",
        }
        
        query = f"SELECT url FROM documents WHERE {stage_conditions.get(stage, '1=0')}"
        result = self.execute(query)
        return [row[0] for row in result] if result else []
    
    def is_url_processed(self, url: str) -> bool:
        query = "SELECT status FROM documents WHERE url = %s"
        result = self.execute_one(query, (url,))
        return result is not None and result[0] == 'success'
    
    def is_stage_completed(self, url: str, stage: int) -> bool:
        stage_fields = {
            3: 'crawl_success',
            4: 'stage4_success',
            5: 'stage5_success',
            6: 'stage6_success',
            7: 'qdrant_point_id IS NOT NULL',
            8: "status = 'success'",
        }
        
        if stage not in stage_fields:
            return False
        
        if stage == 7:
            query = f"SELECT {stage_fields[stage]} FROM documents WHERE url = %s"
            result = self.execute_one(query, (url,))
            return result is not None and result[0]
        
        if stage == 8:
            query = f"SELECT status FROM documents WHERE url = %s AND {stage_fields[stage]}"
            result = self.execute_one(query, (url,))
            return result is not None and result[0] == 'success'
        
        query = f"SELECT {stage_fields[stage]} FROM documents WHERE url = %s"
        result = self.execute_one(query, (url,))
        return result is not None and result[0] is True

        def get_repo_hash(self, repo_url: str) -> Optional[str]:
        """Получает сохраненный SHA/хэш репозитория"""
        query = "SELECT file_sha FROM repo_sources WHERE repo_url = %s"
        result = self.execute_one(query, (repo_url,))
        return result[0] if result else None
    
    def update_repo_hash(self, repo_url: str, file_sha: str) -> None:
        """Обновляет хэш репозитория"""
        query = """
            INSERT INTO repo_sources (repo_url, file_sha, last_scanned_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (repo_url) DO UPDATE SET
                file_sha = EXCLUDED.file_sha,
                last_scanned_at = NOW(),
                updated_at = NOW()
        """
        self.execute(query, (repo_url, file_sha))
    
    def get_processed_urls(self) -> set:
        """Получает все URL со статусом 'success'"""
        query = "SELECT url FROM documents WHERE status = 'success'"
        result = self.execute(query)
        return {row[0] for row in result} if result else set()
    
    def get_urls_by_status(self, status: str) -> List[str]:
        """Получает URL по статусу"""
        query = "SELECT url FROM documents WHERE status = %s"
        result = self.execute(query, (status,))
        return [row[0] for row in result] if result else []
    
    def update_status(self, url: str, status: str, error_message: str = None) -> None:
        """Обновляет статус документа"""
        query = """
            UPDATE documents 
            SET status = %s, 
                updated_at = NOW(),
                last_error = COALESCE(%s, last_error),
                last_error_at = CASE WHEN %s IS NOT NULL THEN NOW() ELSE last_error_at END,
                retry_count = CASE WHEN %s = 'error' THEN retry_count + 1 ELSE retry_count END
            WHERE url = %s
        """
        self.execute(query, (status, error_message, error_message, status, url))
    
    def mark_as_processed(self, url: str) -> None:
        """Помечает документ как полностью обработанный"""
        query = """
            UPDATE documents 
            SET status = 'success', 
                processed_at = NOW(),
                updated_at = NOW()
            WHERE url = %s
        """
        self.execute(query, (url,))

db = Database()