"""Read-only document access through the existing backend service account."""
import re
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile

from django.conf import settings
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from .models import Equipo
from .services import GoogleDriveService, nombre_seguro

FOLDER = "application/vnd.google-apps.folder"
TYPES = {"application/pdf": {".pdf"}, "image/jpeg": {".jpg", ".jpeg"},
         "image/png": {".png"}, "image/webp": {".webp"}}
MAX_BYTES = 64 * 1024 * 1024


class DocumentUnavailable(Exception):
    pass


class EquipoDriveDocuments:
    def __init__(self, equipo):
        self.root = equipo.drive_folder_id
        self.other_roots = set(Equipo.objects.exclude(pk=equipo.pk).exclude(
            drive_folder_id="").values_list("drive_folder_id", flat=True))
        if not self.root or self.root == settings.GOOGLE_DRIVE_ROOT_FOLDER_ID or self.root in self.other_roots:
            raise DocumentUnavailable()
        self.service = GoogleDriveService()

    def metadata(self, file_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", file_id):
            raise DocumentUnavailable()
        try:
            result = self.service.drive.files().get(
                fileId=file_id, fields="id,name,mimeType,parents,trashed,size,capabilities(canDownload)",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            if exc.resp.status in (403, 404):
                raise DocumentUnavailable() from None
            raise
        if result.get("trashed"):
            raise DocumentUnavailable()
        return result

    @staticmethod
    def allowed(item):
        return (item.get("mimeType") in TYPES
                and PurePosixPath(item.get("name", "").lower()).suffix in TYPES[item["mimeType"]]
                and item.get("capabilities", {}).get("canDownload", False)
                and 0 < int(item.get("size", 0)) <= MAX_BYTES)

    def authorize(self, file_id):
        item = self.metadata(file_id)
        if not self.allowed(item):
            raise DocumentUnavailable()
        parents = item.get("parents", [])
        seen = set()
        for _ in range(64):
            # Drive v3 files have one parent. Reject ambiguous or cyclic trees.
            if len(parents) != 1:
                break
            parent = parents[0]
            if parent in seen or parent in self.other_roots:
                break
            seen.add(parent)
            folder = self.metadata(parent)
            if folder.get("mimeType") != FOLDER:
                break
            if parent == self.root:
                return item
            parents = folder.get("parents", [])
        raise DocumentUnavailable()

    def list(self):
        if self.metadata(self.root).get("mimeType") != FOLDER:
            raise DocumentUnavailable()
        pending, seen, result = [(self.root, ())], set(), []
        while pending:
            parent, ancestors = pending.pop()
            if parent in seen or parent in self.other_roots:
                continue
            seen.add(parent)
            if len(seen) > 200:
                raise DocumentUnavailable()
            params = {"q": f"'{parent}' in parents and trashed=false",
                      "fields": "nextPageToken,files(id,name,mimeType,parents,trashed,size,capabilities(canDownload))",
                      "pageSize": 100, "supportsAllDrives": True, "includeItemsFromAllDrives": True}
            if self.service.shared_drive_id:
                params.update(corpora="drive", driveId=self.service.shared_drive_id)
            pages = set()
            while True:
                response = self.service.drive.files().list(**params).execute()
                for item in response.get("files", []):
                    if item.get("trashed") or item.get("parents") != [parent]:
                        continue
                    if item.get("mimeType") == FOLDER:
                        pending.append((item["id"], ancestors + (parent,)))
                    elif self.allowed(item):
                        result.append({"id": item["id"], "name": item["name"],
                                       "folders": ancestors + (parent,)})
                    if len(result) > 2000:
                        raise DocumentUnavailable()
                token = response.get("nextPageToken")
                if not token:
                    break
                if token in pages or len(pages) >= 100:
                    raise DocumentUnavailable()
                pages.add(token)
                params["pageToken"] = token
        return sorted(result, key=lambda item: (item["name"].casefold(), item["id"]))

    def download(self, file_id):
        item = self.authorize(file_id)
        stream = SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b")
        try:
            request = self.service.drive.files().get_media(fileId=file_id, supportsAllDrives=True)
            downloader = MediaIoBaseDownload(stream, request, chunksize=1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=2)
                if stream.tell() > MAX_BYTES:
                    raise DocumentUnavailable()
            # A moved/deleted file must not be served using an earlier listing.
            current = self.authorize(file_id)
            if current["mimeType"] != item["mimeType"]:
                raise DocumentUnavailable()
            stream.seek(0)
            head = stream.read(12)
            valid = {"application/pdf": head.startswith(b"%PDF-"),
                     "image/jpeg": head.startswith(b"\xff\xd8\xff"),
                     "image/png": head.startswith(b"\x89PNG\r\n\x1a\n"),
                     "image/webp": head[:4] == b"RIFF" and head[8:12] == b"WEBP"}
            if not valid[item["mimeType"]]:
                raise DocumentUnavailable()
            stream.seek(0)
            return stream, nombre_seguro(item["name"]), item["mimeType"]
        except HttpError as exc:
            stream.close()
            if exc.resp.status in (403, 404):
                raise DocumentUnavailable() from None
            raise
        except Exception:
            stream.close()
            raise
