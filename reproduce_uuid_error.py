
import json
from uuid import uuid4
from datetime import datetime

class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID): # This is what we might need
            return str(obj)
        return super().default(obj)

try:
    data = {
        "job_ids": [uuid4(), uuid4()],
        "metadata": {
            "user_id": uuid4()
        }
    }
    print(f"Data with UUIDs: {data}")
    # This should fail
    json_str = json.dumps(data)
    print(f"Serialized: {json_str}")
except TypeError as e:
    print(f"Caught expected error: {e}")
