# Spec — Feature #4: Capa de almacenamiento JSON

## Archivos a crear o modificar

| Archivo | Tipo | Descripción |
|---|---|---|
| `src/storage.py` | NUEVO | Funciones genéricas `load()` y `save()` con escritura atómica |
| `src/repositories/__init__.py` | NUEVO | Archivo vacío para hacer `repositories` un paquete |
| `src/repositories/users.py` | NUEVO | `UserRepository` — CRUD para `User` |
| `src/repositories/sessions.py` | NUEVO | `SessionRepository` — CRUD para `Session` |
| `src/repositories/bookings.py` | NUEVO | `BookingRepository` — CRUD para `Booking` |
| `tests/test_storage.py` | NUEVO | Tests unitarios para `load()` y `save()` |
| `tests/test_repositories.py` | NUEVO | Tests unitarios para los tres repositorios |

---

## Implementación

### src/storage.py

Módulo de persistencia genérica en JSON. Es la **única** capa que lee y escribe archivos en `data/`. Los repositorios y la API **nunca** acceden directamente al sistema de archivos; siempre pasan por estas dos funciones.

```python
# ---------- Funciones públicas ----------

def load(entity: str, data_dir: str = "data") -> list[dict[str, Any]]:
    """
    Lee y retorna todos los registros de data/{entity}.json.

    Comportamiento:
      - Si el archivo NO existe, retorna [] (lista vacía) sin lanzar error.
      - Si el archivo existe pero está vacío o contiene JSON inválido,
        la excepción del decoder de json se propaga (no se silencia).

    Args:
        entity: nombre de la entidad (ej. "users", "sessions", "bookings").
        data_dir: directorio donde residen los archivos JSON. Default "data".

    Returns:
        Lista de diccionarios deserializados desde el archivo JSON.

    Raises:
        json.JSONDecodeError: si el archivo contiene JSON malformado.
        OSError: si hay un problema de permisos al leer.
    """

def save(entity: str, records: list[dict[str, Any]], data_dir: str = "data") -> None:
    """
    Escribe atómicamente la lista de registros en data/{entity}.json.

    Estrategia de atomicidad:
      1. Crea el directorio data_dir si no existe (mkdir -p).
      2. Abre un archivo temporal con tempfile.mkstemp en data_dir.
      3. Escribe el JSON con indent=2 y default=str en el archivo temporal.
      4. Cierra el file descriptor.
      5. Reemplaza atómicamente el archivo destino con os.replace(tmp, target).
      6. Si ocurre cualquier excepción durante la escritura, elimina el archivo
         temporal y relanza la excepción.

    Args:
        entity: nombre de la entidad.
        records: lista de diccionarios a persistir.
        data_dir: directorio donde residen los archivos JSON. Default "data".

    Raises:
        OSError: si no se puede crear el directorio o escribir el archivo.
        TypeError: si algún objeto en records no es serializable a JSON
                   (mitigado parcialmente por default=str, pero aún puede fallar
                   con tipos verdaderamente no serializables).

    Notas:
        - El sufijo del archivo temporal es .json y el prefijo es {entity}_.
        - NO deben quedar archivos .tmp residuales tras una escritura exitosa.
    """
```

### src/repositories/__init__.py

```python
# Archivo vacío. Su única función es hacer que Python trate
# src/repositories/ como un paquete importable.
```

### src/repositories/users.py

```python
# ---------- Dependencias ----------
from typing import List, Optional
from src.models.user import User
from src.storage import load, save

# ---------- Clase ----------

class UserRepository:
    """
    Data access para User entities, respaldado por JSON storage.

    Atributos privados:
        _entity: str  — siempre "users"
        _data_dir: str — directorio de datos (inyectado en __init__)
    """

    def __init__(self, data_dir: str = "data") -> None:
        """
        Args:
            data_dir: Directorio donde residen los archivos JSON.
                      Default "data". Inyectable para testing con tmp_path.
        """

    def find_all(self) -> List[User]:
        """
        Retorna todos los usuarios almacenados.

        Usa load() para leer data/{data_dir}/users.json,
        deserializa cada diccionario con User.from_dict().

        Returns:
            Lista de User. Lista vacía si no hay registros.
        """

    def find_by_id(self, id: int) -> Optional[User]:
        """
        Busca un usuario por su id.

        Itera sobre los registros crudos de load() y retorna
        el primero cuyo campo "id" coincida. Si no encuentra
        ninguno, retorna None.

        Args:
            id: identificador entero del usuario.

        Returns:
            User si existe, None si no.

        Raises:
            No lanza excepciones por id no encontrado.
        """

    def save_one(self, obj: User) -> None:
        """
        Inserta o actualiza un usuario (upsert).

        Algoritmo:
          1. Carga todos los registros con load().
          2. Itera buscando un registro con el mismo obj.id.
          3. Si lo encuentra, reemplaza ese diccionario con obj.to_dict().
          4. Si NO lo encuentra, agrega obj.to_dict() al final de la lista.
          5. Persiste la lista completa con save().

        Args:
            obj: instancia de User a persistir.

        Raises:
            ValueError: si obj falla su validación interna al llamar to_dict()
                        (aunque User no lanza error en to_dict; la validación
                        ocurrió en __init__, así que en la práctica no falla aquí).
            OSError: si falla la escritura en disco.
        """

    def delete(self, id: int) -> bool:
        """
        Elimina el usuario con el id dado.

        Algoritmo:
          1. Carga todos los registros con load().
          2. Filtra la lista excluyendo el registro cuyo "id" coincida.
          3. Si la lista se acortó (se eliminó algo), guarda con save() y retorna True.
          4. Si la lista no cambió (id no encontrado), retorna False sin escribir.

        Args:
            id: identificador entero del usuario a eliminar.

        Returns:
            True si se eliminó un registro, False si el id no existía.

        Raises:
            OSError: si falla la escritura en disco.
        """

    def find_by_email(self, email: str) -> Optional[User]:
        """
        Busca un usuario por su email exacto.

        Útil para login y verificación de duplicados.
        No es parte del CRUD mínimo, pero es necesario para features posteriores.

        Args:
            email: string del email a buscar.

        Returns:
            User si existe, None si no.
        """
```

### src/repositories/sessions.py

```python
# ---------- Dependencias ----------
from typing import List, Optional
from src.models.session import Session
from src.storage import load, save

# ---------- Clase ----------

class SessionRepository:
    """
    Data access para Session entities, respaldado por JSON storage.

    Estructura idéntica a UserRepository. Solo cambian:
      - _entity = "sessions"
      - El modelo usado es Session (en lugar de User)
      - No tiene find_by_email (no aplica)

    Métodos:
        __init__(data_dir: str = "data")
        find_all() -> List[Session]
        find_by_id(id: int) -> Optional[Session]
        save_one(obj: Session) -> None
        delete(id: int) -> bool
    """

    def __init__(self, data_dir: str = "data") -> None:
        """Ver UserRepository.__init__."""

    def find_all(self) -> List[Session]:
        """
        Retorna todas las sesiones.
        Usa load("sessions") y deserializa con Session.from_dict().
        """

    def find_by_id(self, id: int) -> Optional[Session]:
        """
        Busca sesión por id. Retorna None si no existe.
        Nunca lanza excepción por id no encontrado.
        """

    def save_one(self, obj: Session) -> None:
        """
        Upsert de sesión: reemplaza si el id ya existe, agrega si no.
        """

    def delete(self, id: int) -> bool:
        """
        Elimina sesión por id.
        Retorna True si eliminó, False si el id no existía.
        """
```

### src/repositories/bookings.py

```python
# ---------- Dependencias ----------
from typing import List, Optional
from src.models.booking import Booking
from src.storage import load, save

# ---------- Clase ----------

class BookingRepository:
    """
    Data access para Booking entities, respaldado por JSON storage.

    Estructura idéntica a UserRepository. Solo cambian:
      - _entity = "bookings"
      - El modelo usado es Booking
      - Métodos adicionales: find_by_user, find_by_session

    Métodos:
        __init__(data_dir: str = "data")
        find_all() -> List[Booking]
        find_by_id(id: int) -> Optional[Booking]
        save_one(obj: Booking) -> None
        delete(id: int) -> bool
        find_by_user(user_id: int) -> List[Booking]
        find_by_session(session_id: int) -> List[Booking]
    """

    def __init__(self, data_dir: str = "data") -> None:
        """Ver UserRepository.__init__."""

    def find_all(self) -> List[Booking]:
        """
        Retorna todas las reservas.
        Usa load("bookings") y deserializa con Booking.from_dict().
        """

    def find_by_id(self, id: int) -> Optional[Booking]:
        """
        Busca reserva por id. Retorna None si no existe.
        Nunca lanza excepción por id no encontrado.
        """

    def save_one(self, obj: Booking) -> None:
        """
        Upsert de reserva: reemplaza si el id ya existe, agrega si no.
        """

    def delete(self, id: int) -> bool:
        """
        Elimina reserva por id.
        Retorna True si eliminó, False si el id no existía.
        """

    def find_by_user(self, user_id: int) -> List[Booking]:
        """
        Retorna todas las reservas de un usuario específico.

        Filtra los registros cuyo campo "user_id" coincida con el argumento.
        Retorna lista vacía si el usuario no tiene reservas.

        Args:
            user_id: identificador del usuario.

        Returns:
            Lista de Booking (puede ser vacía).
        """

    def find_by_session(self, session_id: int) -> List[Booking]:
        """
        Retorna todas las reservas para una sesión específica.

        Filtra los registros cuyo campo "session_id" coincida con el argumento.
        Retorna lista vacía si la sesión no tiene reservas.

        Args:
            session_id: identificador de la sesión.

        Returns:
            Lista de Booking (puede ser vacía).
        """
```

---

## Tests a escribir

### tests/test_storage.py

Tests para las dos funciones de `src/storage.py`. Usar `tmp_path` de pytest como `data_dir` para aislar cada test. No se necesita mockear nada.

| # | Nombre del test | Precondición | Acción | Assertion |
|---|---|---|---|---|
| 1 | `test_load_returns_empty_list_when_file_missing` | No existe `{tmp_path}/users.json` | `load("users", data_dir=str(tmp_path))` | Retorna `[]` |
| 2 | `test_load_returns_records_when_file_exists` | Se crea `users.json` con `[{"id":1,"name":"Alice"}, {"id":2,"name":"Bob"}]` | `load("users", data_dir=str(tmp_path))` | Retorna exactamente la lista original |
| 3 | `test_save_writes_and_load_reads_back` | Directorio vacío | `save("users", [{"id":1,"name":"Charlie"}])`, luego `load(...)` | `load` retorna `[{"id":1,"name":"Charlie"}]` |
| 4 | `test_save_creates_data_directory_if_missing` | NO existe `{tmp_path}/new_data/` | `save("sessions", [], data_dir=str(tmp_path / "new_data"))` | El directorio `new_data` existe y contiene `sessions.json` |
| 5 | `test_save_is_atomic_no_partial_writes` | Directorio vacío | `save("users", [{"id":1}], data_dir=str(tmp_path))` | No existen archivos `*.tmp` en `tmp_path` |
| 6 | `test_save_overwrites_existing_file` | Se guarda `[{"id":1}]` y luego `[{"id":2}]` | `load("users", ...)` | Retorna `[{"id":2}]` — el archivo fue reemplazado, no mergeado |
| 7 | `test_save_preserves_indent_and_encoding` | — | `save("sessions", [{"id":1,"title":"Yoga"}], data_dir=str(tmp_path))` | El archivo contiene `"  "` (indent=2) y saltos de línea; se lee correctamente como UTF-8 |

### tests/test_repositories.py

Tests para los tres repositorios. Cada clase de test usa `tmp_path` como `data_dir` para aislar las pruebas.

Helpers (funciones auxiliares dentro del archivo de tests):

```python
def _make_user(id: int, name: str = "Test", email: str = "test@example.com") -> User:
    return User(id=id, name=name, email=email)

def _make_session(id: int, title: str = "Yoga") -> Session:
    return Session(
        id=id, title=title, instructor="Instructor", style="Hatha",
        starts_at=datetime(2025, 6, 1, 9, 0, 0),
        duration_minutes=60, capacity=20,
    )

def _make_booking(id: int, user_id: int = 1, session_id: int = 1) -> Booking:
    return Booking(id=id, user_id=user_id, session_id=session_id)
```

#### TestUserRepository

| # | Nombre del test | Precondición | Acción | Assertion |
|---|---|---|---|---|
| 1 | `test_save_and_find_all` | Repo vacío | `save_one(user)` con id=1, name="Alice" | `find_all()` retorna lista de 1 elemento; `[0].id == 1`, `[0].name == "Alice"` |
| 2 | `test_save_and_find_by_id` | Se guardan dos usuarios (id=1 Alice, id=2 Bob) | `find_by_id(2)` | Retorna User con `name == "Bob"` y `email == "bob@example.com"` |
| 3 | `test_find_by_id_returns_none_when_not_found` | Se guarda un usuario (id=1) | `find_by_id(999)` | Retorna `None` |
| 4 | `test_find_by_id_returns_none_when_empty` | Repo vacío (sin guardar nada) | `find_by_id(1)` | Retorna `None` |
| 5 | `test_delete_removes_only_the_correct_record` | Se guardan tres usuarios (ids 1, 2, 3) | `delete(2)` | `delete` retorna `True`; `find_all()` tiene solo ids {1, 3} |
| 6 | `test_delete_returns_false_when_not_found` | Se guarda un usuario (id=1) | `delete(999)` | Retorna `False`; `find_all()` sigue teniendo 1 elemento |
| 7 | `test_save_one_updates_existing_record` | Se guarda usuario id=1 name="Alice" | `save_one(User(id=1, name="Alice Updated", ...))` | `find_by_id(1).name == "Alice Updated"`; `find_all()` sigue teniendo longitud 1 |

#### TestSessionRepository

| # | Nombre del test | Precondición | Acción | Assertion |
|---|---|---|---|---|
| 1 | `test_save_and_find_all` | Repo vacío | `save_one(session)` con id=1, title="Morning Yoga" | `find_all()` retorna lista de 1; `[0].title == "Morning Yoga"` |
| 2 | `test_find_by_id_returns_none_when_not_found` | Se guarda session id=1 | `find_by_id(999)` | Retorna `None` |
| 3 | `test_delete_removes_only_the_correct_record` | Se guardan 3 sesiones (ids 1, 2, 3) | `delete(2)` | `delete` retorna `True`; `find_all()` solo ids {1, 3} |
| 4 | `test_delete_returns_false_when_not_found` | Se guarda session id=1 | `delete(999)` | Retorna `False` |
| 5 | `test_save_one_updates_existing_record` | Se guarda session id=1 title="Yoga" | `save_one(session actualizada)` con title="Advanced Yoga" | `find_by_id(1).title == "Advanced Yoga"`; longitud de `find_all()` es 1 |

#### TestBookingRepository

| # | Nombre del test | Precondición | Acción | Assertion |
|---|---|---|---|---|
| 1 | `test_save_and_find_all` | Repo vacío | `save_one(booking)` id=1, user_id=10, session_id=100 | `find_all()[0].user_id == 10`, `[0].session_id == 100` |
| 2 | `test_find_by_id_returns_none_when_not_found` | Se guarda booking id=1 | `find_by_id(999)` | Retorna `None` |
| 3 | `test_delete_removes_only_the_correct_record` | Se guardan 3 bookings (ids 1, 2, 3) | `delete(2)` | `delete` retorna `True`; `find_all()` solo ids {1, 3} |
| 4 | `test_delete_returns_false_when_not_found` | Se guarda booking id=1 | `delete(999)` | Retorna `False` |
| 5 | `test_save_one_updates_existing_record` | Se guarda booking id=1 | `save_one(booking actualizado)` con session_id=200, status="confirmed" | `find_by_id(1).session_id == 200`, `status == "confirmed"`; longitud es 1 |

---

## Dependencias

**No se requieren librerías nuevas.** Todo se construye con módulos de la biblioteca estándar de Python:

- `json` — serialización/deserialización
- `os` — `os.replace()` para atomicidad
- `tempfile` — `tempfile.mkstemp()` para archivo temporal
- `pathlib.Path` — manejo de rutas
- `typing` — `List`, `Optional`, `Dict`, `Any`

Las dependencias de modelos (`src/models/user.py`, `src/models/session.py`, `src/models/booking.py`) deben existir previamente (features #1, #2, #3).

---

## Notas de implementación

1. **`data_dir` parametrizable**: Tanto `load()`/`save()` como los repositorios aceptan `data_dir` (default `"data"`). Esto es crítico para testing: cada test pasa `str(tmp_path)` y así no contamina el sistema de archivos real ni interfiere con otros tests.

2. **Atomicidad**: `save()` debe usar `tempfile.mkstemp` + `os.replace()`. Esto garantiza que nunca haya escrituras parciales visibles: el archivo destino siempre contiene o bien los datos viejos o bien los nuevos completos, nunca un estado intermedio. En caso de excepción durante la escritura, el archivo temporal debe limpiarse.

3. **`save_one` = upsert**: El comportamiento es "insertar o actualizar". Se itera la lista buscando coincidencia por `id`; si se encuentra, se reemplaza el diccionario en esa posición; si no, se agrega al final. Esto es intencional: el `id` es la llave de identidad.

4. **`delete` retorna bool**: `True` si se eliminó un registro, `False` si no se encontró. No lanza excepciones por id inexistente.

5. **`find_by_id` retorna `None`**: Nunca lanza excepción si el id no existe. El caller decide cómo manejar la ausencia.

6. **Repositorios comparten patrón**: Los tres repositorios (`UserRepository`, `SessionRepository`, `BookingRepository`) tienen estructura idéntica. Solo difieren en `_entity` y el modelo de dominio que usan. Esto es deliberado para mantener consistencia y facilitar mantenimiento.

7. **`find_by_email` en UserRepository**: Aunque no está en el CRUD mínimo del feature, es necesario para features #5 (auth/login). Se incluye aquí para que el repo de usuarios esté completo desde el inicio.

8. **`find_by_user` y `find_by_session` en BookingRepository**: Ídem, necesarios para features #7 (reservas de cliente) y #8 (promoción desde waitlist).

9. **El archivo `data/*.json` no se versiona**: Debe estar en `.gitignore`. Los tests no dependen de datos preexistentes en `data/`.

10. **Orden de features**: Esta feature (#4) depende de que los modelos (#1, #2, #3) ya estén implementados con sus métodos `to_dict()` y `from_dict()`.
