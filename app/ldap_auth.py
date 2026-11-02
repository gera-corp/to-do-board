"""Вход через LDAP или Active Directory.

Служебная учётная запись ищет человека по фильтру, затем его пароль проверяется
подключением к каталогу под его именем. Сам пароль доска не хранит.
"""

import logging
import os
import ssl
from dataclasses import dataclass
from typing import Optional

from ldap3 import Connection, NONE, SUBTREE, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

log = logging.getLogger("ldap")

TIMEOUT_SECONDS = 5


class LdapUnavailable(Exception):
    """Каталог не отвечает или служебная учётная запись не подошла."""


@dataclass
class LdapConfig:
    url: str
    base_dn: str
    bind_dn: str
    bind_password: str
    user_filter: str = "(uid={username})"
    login_attr: str = "uid"
    name_attr: str = "displayName"
    ca_file: Optional[str] = None
    link_existing: bool = False

    def __post_init__(self):
        if "{username}" not in self.user_filter:
            raise ValueError("LDAP_USER_FILTER должен содержать {username}")
        if not self.base_dn:
            raise ValueError("Нужен LDAP_BASE_DN")


@dataclass
class LdapUser:
    login: str
    display_name: str


def first_value(attributes: dict, name: str) -> str:
    value = attributes.get(name)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value or "").strip()


class LdapDirectory:
    def __init__(self, config: LdapConfig, connection_factory=None):
        self.config = config
        self.connect = connection_factory or self._make_connection
        self.server = None if connection_factory else self._make_server()

    def _make_server(self) -> Server:
        # Отключать проверку сертификата нельзя: иначе пароли уйдут любому, кто подменит сервер.
        # Явная версия — обход строгого режима Python 3.13 (VERIFY_X509_STRICT): он отвергает
        # внутренние CA за формат расширений, хотя цепочка и подпись верны.
        tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT,
                  ca_certs_file=self.config.ca_file or None)
        return Server(self.config.url, get_info=NONE, connect_timeout=TIMEOUT_SECONDS, tls=tls)

    def _make_connection(self, user: str, password: str) -> Connection:
        # auto_referrals=False: AD отвечает на поиск от корня домена ещё и ссылками на другие разделы.
        return Connection(self.server, user=user, password=password, read_only=True,
                          auto_referrals=False, receive_timeout=TIMEOUT_SECONDS)

    def _find(self, username: str) -> Optional[dict]:
        cfg = self.config
        service = self.connect(cfg.bind_dn, cfg.bind_password)
        try:
            if not service.bind():
                raise LdapUnavailable("служебная учётная запись не подошла")
            search = cfg.user_filter.replace("{username}", escape_filter_chars(username))
            service.search(cfg.base_dn, search, search_scope=SUBTREE, size_limit=2,
                           attributes=[cfg.login_attr, cfg.name_attr])
            entries = [e for e in service.response if e.get("type") == "searchResEntry"]
        finally:
            service.unbind()
        # Несколько совпадений значат неудачный фильтр: лучше отказать, чем войти не тем человеком.
        return entries[0] if len(entries) == 1 else None

    def authenticate(self, username: str, password: str) -> Optional[LdapUser]:
        """Проверяет логин и пароль. Возвращает None, если они не подошли."""
        # Пустой пароль — анонимное подключение: LDAP считает его успешным для любого логина.
        if not username or not password or "\x00" in username or "\x00" in password:
            return None
        try:
            entry = self._find(username)
            if entry is None:
                return None
            attempt = self.connect(entry["dn"], password)
            try:
                if not attempt.bind():
                    return None
            finally:
                attempt.unbind()
        except (LDAPException, OSError) as exc:
            raise LdapUnavailable(str(exc)) from exc
        attributes = entry["attributes"]
        login = first_value(attributes, self.config.login_attr) or username
        return LdapUser(login=login, display_name=first_value(attributes, self.config.name_attr) or login)


def make_directory() -> Optional[LdapDirectory]:
    url = os.environ.get("LDAP_URL")
    if not url:
        return None
    env = os.environ.get
    return LdapDirectory(LdapConfig(
        url=url,
        base_dn=env("LDAP_BASE_DN", ""),
        bind_dn=env("LDAP_BIND_DN", ""),
        bind_password=env("LDAP_BIND_PASSWORD", ""),
        user_filter=env("LDAP_USER_FILTER") or "(uid={username})",
        login_attr=env("LDAP_LOGIN_ATTR") or "uid",
        name_attr=env("LDAP_NAME_ATTR") or "displayName",
        ca_file=env("LDAP_CA_FILE") or None,
        link_existing=(env("LDAP_LINK_EXISTING") or "").lower() in ("1", "true", "yes"),
    ))
