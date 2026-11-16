from telethon import utils

BLOCKED_SECTIONS = {"messages", "upload", "stories"}

ALLOWED_EXCEPTIONS = {
    "GetDhConfigRequest",
}

BLOCKED_REQUESTS = {
    ("channels", "GetMessagesRequest"),
    ("channels", "ReadHistoryRequest"),
    ("channels", "ReadMessageContentsRequest"),
    ("channels", "DeleteMessagesRequest"),
    ("channels", "DeleteHistoryRequest"),
    ("channels", "SearchPostsRequest"),
    ("channels", "GetForumTopicsRequest"),
    ("channels", "ExportMessageLinkRequest"),
}


class ChatAccessBlocked(PermissionError):
    pass


def check_request(request) -> None:
    section = type(request).__module__.rsplit(".", 1)[-1]
    name = type(request).__name__
    blocked = (section in BLOCKED_SECTIONS and name not in ALLOWED_EXCEPTIONS) or (
        (section, name) in BLOCKED_REQUESTS
    )
    if blocked:
        raise ChatAccessBlocked(f"Запрещено охранником: {section}.{name} — боту доступны только звонки")


def lock_to_calls_only(client):
    original_call = client._call

    async def guarded_call(sender, request, ordered=False, flood_sleep_threshold=None):
        for item in request if utils.is_list_like(request) else [request]:
            check_request(item)
        return await original_call(sender, request, ordered=ordered, flood_sleep_threshold=flood_sleep_threshold)

    client._call = guarded_call
    client._calls_only_guard = True
    return client
