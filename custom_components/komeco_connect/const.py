"""Constants for the Komeco Connect integration."""

from datetime import timedelta

DOMAIN = "komeco_connect"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_DEVICE_ID = "device_id"
CONF_PLACE_ID = "place_id"
CONF_ID_TOKEN = "id_token"
CONF_ACCESS_TOKEN = "access_token"
CONF_SUB = "sub"

PLATFORMS = ["water_heater", "switch", "number", "sensor", "binary_sensor"]

DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

AWS_REGION = "us-east-1"
AWS_SERVICE = "execute-api"
AWS_IOT_DATA_SERVICE = "iotdata"
COGNITO_USER_POOL_ID = "us-east-1_puOF9gPhT"
COGNITO_CLIENT_ID = "hblm0vu86a95plht5dgvgvjtk"
COGNITO_IDENTITY_POOL_ID = "us-east-1:7894260e-cd04-4bec-8f99-280395d7fb5f"
COGNITO_UHASH_PASSPHRASE = "KomecoIotPlatformAPI"

ENDPOINTS = {
    "prod-device": "https://ra6bfq1k32.execute-api.us-east-1.amazonaws.com/prod",
    "prod-command": "https://1gjj9ktxm0.execute-api.us-east-1.amazonaws.com/prod",
    "prod-commandHistory": "https://cnywguj5vj.execute-api.us-east-1.amazonaws.com/prod",
    "prod-dataset": "https://8p3agv1oz4.execute-api.us-east-1.amazonaws.com/prod",
    "prod-places": "https://0qgaonfab8.execute-api.us-east-1.amazonaws.com/prod",
}

IOT_DATA_ENDPOINT = "https://a81xba68rb3e-ats.iot.us-east-1.amazonaws.com"

DEVICE_TYPE_LABELS: dict[int, str] = {
    1: "Sistemas fotovoltaicos",
    2: "Ar condicionado",
    3: "Bombas e Pressurizadores",
    4: "Aquecedor a gas",
    5: "Aquecedor solar",
    6: "Bomba de calor",
    7: "Carregador veicular",
    8: "Acionador",
    9: "Desumidificador de ambiente",
    10: "Aquecedor de Ambiente",
    12: "Sistemas de filtragem",
    14: "Robo de Piscina",
    17: "Iluminacao de Piscina",
}

# Current implementation support. Expand this set as new platforms are added.
SUPPORTED_DEVICE_TYPES: set[int] = {4}
