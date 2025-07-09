import requests
import json

def getServer():
    try:
        return requests.get(
            url="https://api.gofile.io/getServer"
        ).json()
    except json.decoder.JSONDecodeError:
        # Handle the case where the response is not a valid JSON
        # You can add retries or other error handling here
        return None

def uploadFile(file: str, token: str = None, folderId: str = None, description: str = None, password: str = None, tags: str = None, expire: int = None):
    server_response = getServer()
    if not server_response or server_response.get("status") != "ok":
        raise Exception("Failed to get a server")

    server = server_response["data"]["server"]

    _data = {
        "token": token,
        "folderId": folderId,
        "description": description,
        "password": password,
        "tags": tags,
        "expire": expire
    }

    _file = {
        "file": open(file, "rb")
    }

    response = requests.post(
        url=f"https://{server}.gofile.io/uploadFile",
        data=_data,
        files=_file
    ).json()

    if response.get("status") == "ok":
        return response["data"]
    else:
        raise Exception(f"Failed to upload file: {response}")
