from environ import Env


env = Env(
    ALLOWED_HOSTS=(str, "frikanalen.no,forrige.frikanalen.no,beta.frikanalen.no"),
    SMTP_SERVER=(str),
    SECRET_KEY=(str),
)