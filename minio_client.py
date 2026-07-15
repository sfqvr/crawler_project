from hashlib import sha256
from io import BytesIO
import json
import pandas as pd

from minio import Minio
from minio.error import S3Error

INPUT_FILENAMES_PREFIX = "demoTest" 

class MinIOStorage:
    def __init__(
        self,
        endpoint="localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
    ):
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def create_bucket(self, bucket: str):
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)

    def load_dataframe(self, bucket_name: str, prefix: str) -> pd.DataFrame:
        rows = []

        for obj in self.client.list_objects(
            bucket_name,
            prefix=prefix,
            recursive=True,
        ):
            response = self.client.get_object(bucket_name, obj.object_name)
            try:
                rows.append(json.load(response))
            finally:
                response.close()
                response.release_conn()

        return pd.DataFrame(rows)
    
    # def create_jsonl(self, bucket: str, object_name: str):
        data = BytesIO(b"")

        self.client.put_object(
            bucket,
            object_name,
            data,   
            length=0,
            content_type="application/json",
        )
    
    def append_json(self, bucket_name: str, prefix: str, obj) -> None:
        url = obj["url"]
        document_id = sha256(url.encode("utf-8")).hexdigest()
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")

        self.client.put_object(
            bucket_name=bucket_name,
            object_name=f"{prefix}/{document_id}.json",
            data=BytesIO(payload),
            length=len(payload),
            content_type="application/json",
        )

    def append_html(self, bucket_name: str, prefix: str, url: str, html: str, ) -> None:
        document_id = sha256(url.encode("utf-8")).hexdigest()
        payload = html.encode("utf-8")

        self.client.put_object(
            bucket_name=bucket_name,
            object_name=f"{prefix}/{document_id}.html",
            data=BytesIO(payload),
            length=len(payload),
            content_type="text/html; charset=utf-8",
        )
    
    def get_html(self, bucket_name: str, prefix: str, url: str) -> str | None:
        """
        Retrieves the HTML previously stored via append_html for the given url.
        Returns None if the object doesn't exist (e.g. not yet crawled, or wrong url/prefix).
        """
        document_id = sha256(url.encode("utf-8")).hexdigest()
        object_name = f"{prefix}/{document_id}.html"

        try:
            response = self.client.get_object(
                bucket_name=bucket_name,
                object_name=object_name,
            )
            try:
                html = response.read().decode("utf-8")
            finally:
                response.close()
                response.release_conn()
            return html

        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            raise

    def append_markdown(self, bucket_name: str, prefix: str, url: str, markdown: str) -> None:
        document_id = sha256(url.encode("utf-8")).hexdigest()
        payload = markdown.encode("utf-8")

        self.client.put_object(
            bucket_name=bucket_name,
            object_name=f"{prefix}/{document_id}.md",
            data=BytesIO(payload),
            length=len(payload),
            content_type="text/markdown; charset=utf-8",
        )

    def get_markdown(self, bucket_name: str, prefix: str, url: str) -> str | None:
        """
        Retrieves the markdown previously stored via append_markdown for the given url.
        Returns None if the object doesn't exist.
        """
        document_id = sha256(url.encode("utf-8")).hexdigest()
        object_name = f"{prefix}/{document_id}.md"

        try:
            response = self.client.get_object(
                bucket_name=bucket_name,
                object_name=object_name,
            )
            try:
                markdown = response.read().decode("utf-8")
            finally:
                response.close()
                response.release_conn()
            return markdown

        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            raise


