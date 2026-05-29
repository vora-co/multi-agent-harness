# CHECKPOINTS — Criterios de "estado final correcto"

El reviewer valida cada punto. Un solo FAIL bloquea el APPROVED.

## Código
- [ ] Los archivos nuevos están en src/ o tests/ según corresponda
- [ ] No hay print() de debug sin comentario explicativo
- [ ] No hay TODOs sin contexto (fecha + razón)
- [ ] Sigue la convención de nombres en docs/conventions.md

## Tests
- [ ] Existe al menos un test por función pública nueva
- [ ] `python -m pytest tests/ -v` termina con 0 errores y 0 failures
- [ ] Los tests no dependen de estado externo sin limpiarlo en teardown

## Documentación
- [ ] Cada función nueva tiene docstring de una línea
- [ ] progress/impl_<id>.md existe y lista los archivos tocados

## Integración
- [ ] El código nuevo no rompe tests de features anteriores (pytest sobre todo tests/)
- [ ] No hay imports circulares
