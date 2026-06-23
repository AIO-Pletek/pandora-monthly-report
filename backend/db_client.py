"""
Read-only MySQL client for Pandora FMS database.

Queries the Pandora database directly to get data the Community
Edition API does not expose:

  - Groups from ``tgrupo``
  - Agent→group mapping from ``tagente``
  - Agent→module mapping from ``tagente_modulo``

Database connection is lazy — only created on first use.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

import pymysql

logger = logging.getLogger(__name__)


class PandoraDB:
    """Read-only connection to Pandora FMS MySQL database.

    Usage::

        db = PandoraDB(host="localhost", user="devops",
                       password="...", database="pandora")
        agents = db.get_agents_by_group(36)
        modules = db.get_agent_modules(453)
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        database: str,
        port: int = 3306,
    ) -> None:
        self._host = host
        self._user = user
        self._password = password
        self._database = database
        self._port = port
        self._conn: pymysql.Connection | None = None

    @contextmanager
    def _cursor(self):
        """Get a database cursor, auto-reconnecting if needed."""
        try:
            if self._conn is None or not self._conn.open:
                self._conn = pymysql.connect(
                    host=self._host,
                    user=self._user,
                    password=self._password,
                    database=self._database,
                    port=self._port,
                    charset="utf8",
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10,
                    read_timeout=30,
                )
                logger.info("Connected to Pandora DB %s@%s:%d/%s",
                            self._user, self._host, self._port, self._database)
            cursor = self._conn.cursor()
            yield cursor
        except pymysql.MySQLError as e:
            logger.error("Database error: %s", e)
            self._conn = None
            raise
        finally:
            if cursor:
                cursor.close()

    def query(self, sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
        """Execute a read-only query and return results as list of dicts."""
        with self._cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # ── Group queries ───────────────────────────────────────────────────

    def get_groups(self) -> list[dict]:
        """Return all agent groups: id_grupo, nombre, icon, etc."""
        return self.query(
            "SELECT id_grupo, nombre FROM tgrupo ORDER BY nombre"
        )

    def get_agent_count_by_group(self, id_grupo: int) -> int:
        """Return how many agents are in a group."""
        rows = self.query(
            "SELECT COUNT(*) AS cnt FROM tagente WHERE id_grupo = %s AND disabled = 0",
            (id_grupo,),
        )
        return rows[0]["cnt"] if rows else 0

    # ── Agent queries ───────────────────────────────────────────────────

    def get_agents(self) -> list[dict]:
        """Return ALL agents with group info."""
        return self.query("""
            SELECT
                a.id_agente,
                a.nombre AS alias,
                a.direccion,
                a.comentarios,
                a.id_grupo,
                g.nombre AS grupo_nombre,
                a.id_os,
                a.ultimo_contacto,
                a.intervalo
            FROM tagente a
            LEFT JOIN tgrupo g ON a.id_grupo = g.id_grupo
            WHERE a.disabled = 0
            ORDER BY a.nombre
        """)

    def get_agents_by_group(self, id_grupo: int) -> list[dict]:
        """Return agents in a specific group."""
        return self.query(
            """
            SELECT id_agente, nombre AS alias, direccion, comentarios,
                   id_grupo, id_os, ultimo_contacto, intervalo
            FROM tagente
            WHERE id_grupo = %s AND disabled = 0
            ORDER BY nombre
            """,
            (id_grupo,),
        )

    # ── Module queries ──────────────────────────────────────────────────

    def get_agent_modules(self, agent_id: int) -> list[dict]:
        """Return all modules for an agent: module ID + name."""
        return self.query(
            """
            SELECT id_agente_modulo, nombre, descripcion, id_tipo_modulo
            FROM tagente_modulo
            WHERE id_agente = %s AND disabled = 0
            ORDER BY id_agente_modulo
            """,
            (agent_id,),
        )
