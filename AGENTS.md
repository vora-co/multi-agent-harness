# AGENTS.md — Mapa de navegación

> Punto de entrada para cualquier agente. Lee solo lo que necesites.

## 1. Antes de empezar (obligatorio)
1. Ejecuta `python -m pytest tests/ -v` — debe pasar sin errores.
2. Lee `progress/current.md` — estado de la última sesión.
3. Lee `feature_list.json` — elige una feature con `status: "pending"`.

## 2. Mapa del repositorio

| Archivo                    | Qué contiene                            | Cuándo leerlo         |
|----------------------------|-----------------------------------------|-----------------------|
| `feature_list.json`        | Tareas con estado                       | Siempre al empezar    |
| `progress/current.md`      | Estado sesión activa                    | Siempre al empezar    |
| `progress/history.md`      | Bitácora de sesiones anteriores         | Si necesitas contexto |
| `docs/architecture.md`     | Qué significa hacer buen trabajo        | Antes de implementar  |
| `docs/conventions.md`      | Estilo, nombres, estructura             | Antes de escribir código |
| `docs/verification.md`     | Cómo verificar que funciona             | Antes de declarar done |
| `CHECKPOINTS.md`           | Criterios objetivos de done             | Reviewer siempre      |

## 3. Reglas duras
- Una sola feature a la vez. No mezcles cambios.
- No declares done sin tests verdes.
- Documenta en progress/current.md mientras trabajas, no al final.
- Si no sabes algo, busca en docs/ antes de inventarlo.

## 4. Flujo de roles

```
Leader → lee feature_list → spawn_implementer → spawn_reviewer → cierra sesión
Implementer → lee docs/ → escribe src/ y tests/ → corre tests → progress/impl_<id>.md
Reviewer → lee CHECKPOINTS → valida → progress/review_<id>.md → APPROVED o REJECTED
```
