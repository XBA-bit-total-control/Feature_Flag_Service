import hashlib
import struct


def deterministic_hash(user_id: str, flag_key: str, salt: str) -> int:
    """Вычислить детерминированный хеш для распределения пользователя.

    Возвращает число от 0 до 99 (bucket).
    """
    message = f"{user_id}:{flag_key}:{salt}"
    hash_bytes = hashlib.sha256(message.encode("utf-8")).digest()
    hash_int = struct.unpack("I", hash_bytes[:4])[0]
    return hash_int % 100


def resolve_bucket(bucket: int, weights: list[float]) -> int:
    """Определить вариант на основе bucket и весов.

    Args:
        bucket: Число от 0 до 99, полученное из хеша.
        weights: Список весов вариантов (сумма может быть любой).

    Returns:
        Индекс варианта, которому принадлежит bucket.
    """
    total_weight = sum(weights)
    cumulative = 0.0
    for index, weight in enumerate(weights):
        cumulative += (weight / total_weight) * 100
        if bucket < cumulative:
            return index
    return len(weights) - 1
