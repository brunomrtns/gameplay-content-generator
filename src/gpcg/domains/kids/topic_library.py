"""Kids Topic Library — structured categories and seed topics for ideation.

This is a static, extensible library of categories and seed topics that
the AI ideation agent uses as inspiration. It is NOT an exhaustive list
of every possible topic — it's a starting point that the LLM expands on.

Structure:
    Category → Seed topics → LLM expansion → KidsIdeas

The library is intentionally small and extensible. New categories and
seeds can be added without code changes (just add to the dict).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TopicSeed:
    """A seed topic in the library."""
    title_hint: str  # e.g. "Por que os polvos têm três corações?"
    description: str = ""  # what the video would cover
    age_ranges: list[str] = field(default_factory=lambda: ["3-6", "7-10"])


@dataclass
class TopicCategory:
    """A category in the topic library."""
    name: str  # e.g. "animals"
    display_name: str  # e.g. "Animais"
    description: str  # what this category covers
    seeds: list[TopicSeed] = field(default_factory=list)


# ── The library ──────────────────────────────────────────────────────────────

_TOPIC_LIBRARY: list[TopicCategory] = [
    TopicCategory(
        name="animals",
        display_name="Animais",
        description="Curiosidades sobre animais: comportamento, corpo, habitat",
        seeds=[
            TopicSeed("Por que os polvos têm três corações?", "Sistema circulatório dos polvos"),
            TopicSeed("Como os camaleões mudam de cor?", "Camuflagem e comunicação"),
            TopicSeed("O que os pandas comem?", "Dieta do panda gigante"),
            TopicSeed("Por que os flamingos são rosados?", "Cor das penas dos flamingos"),
            TopicSeed("Como os morcegos enxergam no escuro?", "Ecolocalização"),
        ],
    ),
    TopicCategory(
        name="science",
        display_name="Ciência",
        description="Experimentos simples e fenômenos científicos explicados para crianças",
        seeds=[
            TopicSeed("Por que o céu é azul?", "Dispersão da luz no céu"),
            TopicSeed("Como se forma um arco-íris?", "Refração e reflexão da luz"),
            TopicSeed("O que é gravidade?", "Força que puxa objetos para baixo"),
            TopicSeed("Por que o gelo flutua na água?", "Densidade do gelo vs água"),
        ],
    ),
    TopicCategory(
        name="space",
        display_name="Espaço",
        description="Planetas, estrelas, o sol, a lua e o universo",
        seeds=[
            TopicSeed("Quantos planetas tem no sistema solar?", "Os 8 planetas"),
            TopicSeed("Por que a lua muda de forma?", "Fases da lua"),
            TopicSeed("O que é uma estrela cadente?", "Meteoros e meteoritos"),
            TopicSeed("Como é o planeta Marte?", "Características de Marte"),
            TopicSeed("O que é o buraco negro?", "Buracos negros explicados para crianças"),
        ],
    ),
    TopicCategory(
        name="dinosaurs",
        display_name="Dinossauros",
        description="Tiranossauro, tricerátops, braquiossauro e outros dinossauros",
        seeds=[
            TopicSeed("Quanto media o Tiranossauro Rex?", "Tamanho do T-Rex"),
            TopicSeed("O que os dinossauros comiam?", "Dieta dos dinossauros"),
            TopicSeed("Como os dinossauros desapareceram?", "Extinção dos dinossauros"),
            TopicSeed("Qual o maior dinossauro do mundo?", "Braquiossauro e outros gigantes"),
        ],
    ),
    TopicCategory(
        name="nature",
        display_name="Natureza",
        description="Florestas, oceanos, vulcões, clima e meio ambiente",
        seeds=[
            TopicSeed("Como se forma um vulcão?", "Erupções vulcânicas"),
            TopicSeed("Por que as folhas mudam de cor no outono?", "Clorofila e estações"),
            TopicSeed("O que é uma floresta tropical?", "Florestas tropicais e biodiversidade"),
            TopicSeed("De onde vem a chuva?", "Ciclo da água"),
        ],
    ),
    TopicCategory(
        name="ocean",
        display_name="Oceano",
        description="Animais marinhos, corais, profundezas do mar",
        seeds=[
            TopicSeed("Quem vive no fundo do mar?", "Criaturas das profundezas"),
            TopicSeed("Como os peixes respiram debaixo d'água?", "Brânquias dos peixes"),
            TopicSeed("O que são os corais?", "Recifes de coral"),
            TopicSeed("Qual é o maior animal do mundo?", "Baleia azul"),
        ],
    ),
    TopicCategory(
        name="human_body",
        display_name="Corpo Humano",
        description="Como funciona o corpo: ossos, coração, cérebro, sentidos",
        seeds=[
            TopicSeed("Quantos ossos tem o corpo humano?", "Esqueleto humano"),
            TopicSeed("Como funciona o coração?", "Sistema circulatório"),
            TopicSeed("Por que sentimos sono?", "Sono e descanso"),
            TopicSeed("Como o cérebro funciona?", "O cérebro explicado para crianças"),
        ],
    ),
    TopicCategory(
        name="history",
        display_name="História",
        description="Eventos históricos e civilizações antigas explicados para crianças",
        seeds=[
            TopicSeed("Como os egípcios construíram as pirâmides?", "Pirâmides do Egito"),
            TopicSeed("Quem foram os vikings?", "Povo viking"),
            TopicSeed("Como as pessoas viviam antigamente?", "Vida na antiguidade"),
        ],
    ),
    TopicCategory(
        name="geography",
        display_name="Geografia",
        description="Países, continentes, montanhas e rios do mundo",
        seeds=[
            TopicSeed("Quantos continentes existem?", "Continentes do mundo"),
            TopicSeed("Qual é o maior rio do mundo?", "Rio Amazonas e rio Nilo"),
            TopicSeed("O que é um deserto?", "Desertos e vida no deserto"),
            TopicSeed("Qual a montanha mais alta do mundo?", "Monte Everest"),
        ],
    ),
    TopicCategory(
        name="vehicles",
        display_name="Veículos",
        description="Carros, aviões, trens, navios e como funcionam",
        seeds=[
            TopicSeed("Como os aviões voam?", "Princípios do voo"),
            TopicSeed("O que faz um carro andar?", "Motores de combustão"),
            TopicSeed("Como os submarinos mergulham?", "Submarinos e pressão"),
        ],
    ),
    TopicCategory(
        name="food",
        display_name="Comida",
        description="De onde vem a comida, nutrição e curiosidades alimentares",
        seeds=[
            TopicSeed("De onde vem o chocolate?", "Do cacau ao chocolate"),
            TopicSeed("Como o mel é feito?", "Abelhas e produção de mel"),
            TopicSeed("Por que precisamos comer verduras?", "Nutrição e vitaminas"),
        ],
    ),
    TopicCategory(
        name="colors",
        display_name="Cores",
        description="Cores, arco-íris, mistura de cores e como enxergamos",
        seeds=[
            TopicSeed("Quantas cores tem o arco-íris?", "Cores do arco-íris"),
            TopicSeed("Como enxergamos as cores?", "Olhos e percepção de cor"),
            TopicSeed("O que acontece quando misturamos cores?", "Mistura de cores"),
        ],
    ),
    TopicCategory(
        name="numbers",
        display_name="Números",
        description="Matemática divertida: contagem, formas, padrões",
        seeds=[
            TopicSeed("Como as pessoas contavam antigamente?", "História dos números"),
            TopicSeed("O que são números pares e ímpares?", "Pares e ímpares"),
            TopicSeed("Como se conta até 100?", "Contagem para crianças"),
        ],
    ),
    TopicCategory(
        name="curiosity",
        display_name="Curiosidades",
        description="Curiosidades gerais que despertam o interesse das crianças",
        seeds=[
            TopicSeed("Por que bocejamos?", "Por que bocejamos"),
            TopicSeed("De onde vem o vento?", "Vento e pressão atmosférica"),
            TopicSeed("Por que o gelo derrete?", "Estados da matéria"),
            TopicSeed("Como os relógios sabem a hora?", "Medição do tempo"),
        ],
    ),
]


def get_all_categories() -> list[TopicCategory]:
    """Return all categories in the library."""
    return list(_TOPIC_LIBRARY)


def get_category(name: str) -> TopicCategory | None:
    """Get a category by name (e.g. 'animals')."""
    for cat in _TOPIC_LIBRARY:
        if cat.name == name:
            return cat
    return None


def get_category_names() -> list[str]:
    """Return all category names."""
    return [cat.name for cat in _TOPIC_LIBRARY]


def get_seeds_for_category(name: str) -> list[TopicSeed]:
    """Get seed topics for a category."""
    cat = get_category(name)
    if not cat:
        return []
    return list(cat.seeds)


def get_all_seeds() -> dict[str, list[TopicSeed]]:
    """Get all seeds grouped by category name."""
    return {cat.name: list(cat.seeds) for cat in _TOPIC_LIBRARY}
