"""CLI básico para la aplicación de notas con comandos add y list."""

import argparse
import sys

from src.notes import Note
from src.storage import load, save


def _next_id(notes_data: list) -> int:
    """Devuelve el siguiente id autoincremental basado en los datos existentes."""
    if not notes_data:
        return 1
    max_id = 0
    for note_dict in notes_data:
        try:
            nid = int(note_dict["id"])
            if nid > max_id:
                max_id = nid
        except (ValueError, TypeError):
            pass
    return max_id + 1


def cmd_add(args: argparse.Namespace) -> None:
    """Ejecuta el comando 'add': crea una nota y la guarda atómicamente."""
    notes_data = load()
    new_id = _next_id(notes_data)
    note = Note(id=new_id, title=args.title, body=args.body or "")
    notes_data.append(note.to_dict())
    save(notes_data)
    print(f"Nota agregada: [{new_id}] {note.title}")


def cmd_list(args: argparse.Namespace) -> None:
    """Ejecuta el comando 'list': carga y muestra todas las notas."""
    notes_data = load()
    if not notes_data:
        print("No hay notas guardadas.")
        return
    for note_dict in notes_data:
        note = Note.from_dict(note_dict)
        print(f"[{note.id}] {note.title}")
        if note.body:
            print(f"    {note.body}")
        print(f"    Creada: {note.created_at.isoformat()}")
        print()


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos con subcomandos add y list."""
    parser = argparse.ArgumentParser(
        description="Sistema de notas por CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcomando: add
    parser_add = subparsers.add_parser("add", help="Agrega una nueva nota")
    parser_add.add_argument("title", help="Título de la nota")
    parser_add.add_argument(
        "--body", "-b", default=None, help="Contenido opcional de la nota"
    )
    parser_add.set_defaults(func=cmd_add)

    # Subcomando: list
    parser_list = subparsers.add_parser("list", help="Lista todas las notas")
    parser_list.set_defaults(func=cmd_list)

    return parser


def main(argv: list = None) -> None:
    """Punto de entrada: parsea argumentos y ejecuta el comando correspondiente."""
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
