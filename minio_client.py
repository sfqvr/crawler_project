from io import BytesIO
import json

from minio import Minio
from minio.error import S3Error


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
    
    def create_jsonl(self, bucket: str, object_name: str):
        data = BytesIO(b"")

        self.client.put_object(
            bucket,
            object_name,
            data,   
            length=0,
            content_type="application/json",
        )
    
    def append_json(self, bucket, object_name, obj):

        line = json.dumps(obj, ensure_ascii=False) + "\n"

        try:
            response = self.client.get_object(bucket, object_name)
            old_data = response.read()

        except S3Error:
            old_data = b""

        new_data = old_data + line.encode("utf-8")

        self.client.put_object(
            bucket,
            object_name,
            BytesIO(new_data),
            len(new_data),
            content_type="application/json",
        )

    def read_jsonl(self, bucket, object_name):

        response = self.client.get_object(bucket, object_name)

        for line in response:

            if line.strip():

                yield json.loads(line)