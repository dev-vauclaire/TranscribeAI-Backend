from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    # URL de connexion à la base de données.
    url: str = field(repr=False)

    # Activez ou désactivez l'écho des requêtes SQL dans les journaux de log
    echo: bool = False

    # Limite le nombre de connexions simultanées à la base de données.
    pool_size: int = 3

    # Limite le nombre de connexions supplémentaires pouvant être créées au-delà de la taille du pool.
    max_overflow: int = 0

    # Limite le temps d'attente pour obtenir une connexion à partir du pool avant de lever une exception.
    pool_timeout_seconds: float = 30.0