"""By voice every step of a procedure is narrated (decision of 23/11): no 2-step cut, no 450-character limit,
no step cut at 160 characters and no "El resto está en el chat." for lists. Other long answers keep their summary."""
from backend.app.assistant.speech import REST, spoken_summary


def test_every_step_of_a_long_procedure_is_read_in_order():
    steps = [f"Haz la acción número {n} y espera a que termine antes de continuar con la siguiente" for n in range(1, 16)]
    answer = "«Instalación limpia de Windows» (15 pasos):\n" + "\n".join(f"{n}. {s}." for n, s in enumerate(steps, 1))
    speech = spoken_summary(answer)
    assert speech.startswith("«Instalación limpia de Windows» tiene 15 pasos. Paso 1: Haz la acción número 1")
    positions = [speech.index(f"Paso {n}: Haz la acción número {n} ") for n in range(1, 16)]
    assert positions == sorted(positions) and REST not in speech


def test_a_long_step_is_read_whole():
    long_step = "Abre el panel de control, entra en Sistema y seguridad, después en Herramientas administrativas, " \
                "abre Servicios, busca Windows Update, ábrelo con doble clic y cambia el tipo de inicio a Automático"
    speech = spoken_summary(f"«Actualizaciones» (1 paso):\n1. {long_step}.")
    assert speech == f"«Actualizaciones» tiene 1 paso. Paso 1: {long_step}."


def test_a_step_on_several_lines_is_not_cut_and_later_steps_are_not_skipped():
    answer = ("«VPN» (3 pasos):\n1. Abre el cliente VPN\n   y elige el perfil de la empresa.\n"
              "2. Escribe tu usuario.\n3. Pulsa Conectar.\n¿Quieres que te explique algún paso?")
    assert spoken_summary(answer) == ("«VPN» tiene 3 pasos. Paso 1: Abre el cliente VPN y elige el perfil de la empresa. "
                                      "Paso 2: Escribe tu usuario. Paso 3: Pulsa Conectar.")


def test_sources_and_substeps_are_still_not_read():
    answer = ("«Red» (2 pasos):\n1. Conecta el cable (incluye: puerto 1; puerto 2).\n2. Revisa la luz verde.\n"
              "[Fuente: Manual de red]")
    assert spoken_summary(answer) == "«Red» tiene 2 pasos. Paso 1: Conecta el cable. Paso 2: Revisa la luz verde."


def test_other_long_answers_keep_their_spoken_summary():
    answer = " ".join(f"Esta es la oración número {n} de una explicación general bastante larga." for n in range(1, 20))
    speech = spoken_summary(answer)
    assert speech.endswith(REST) and len(speech) < len(answer)
