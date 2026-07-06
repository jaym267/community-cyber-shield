// Lightweight EN/ES string dictionary for Sentinal's chrome (headings,
// buttons, labels). The AI report content itself is generated in the chosen
// language by the backend (?lang=es on /api/neighborhood), and assistance
// resource descriptions arrive bilingual (desc / desc_es) — this file only
// covers the app's own strings. v1 scope: the strings a resident actually
// reads on every visit; deep content (indicator explainers, legal modal)
// remains English for now and is flagged in the README roadmap.

export const STRINGS = {
  en: {
    kicker: "✳ ENVIRONMENTAL FIELD SURVEY ✳",
    tagline: "Environmental justice by zip code",
    heroLead:
      "Every neighborhood has an environmental story. Enter a zip code to see air quality, pollution burden, industrial sites, and green space — turned into a plain-language report card.",
    searchPlaceholder: "Enter a 5-digit zip code",
    searchBtn: "Search",
    try: "Try",
    recent: "Recent",
    fact1: "Environmental indicators tracked",
    fact2: "Plain-language grade for every zip",
    fact3: "Real-time air quality & official hazard alerts",
    zipCode: "Zip code",
    gradeLabel: "GRADE",
    outOf100: "out of 100",
    shareReport: "Share this report",
    pinCompare: "Pin to compare",
    unpinCompare: "Unpin comparison",
    rightNowIn: "Right now in",
    liveConditions: "live measured conditions",
    aqiLabel: "Air quality index",
    outlook: "3-day outlook (NWS)",
    noAlerts: "✓ No active weather hazard alerts for this area right now.",
    whatGradeMeans: "What the grade means",
    whatYouCanDo: "What you can do",
    takeAction: "Take action",
    takeActionHint: "Turn this report into something officials have to answer.",
    copyLetter: "Copy a letter to your officials",
    letterCopied: "Letter copied — paste it into an email",
    reportViolation: "Report a violation to EPA",
    findOfficials: "Find your elected officials",
    printReport: "Print this report",
    actionNote:
      "The letter is pre-filled with this report's actual numbers — paste it into an email, add your name, and send. Print gives you a clean copy to bring to a council or neighborhood meeting.",
    hazardHelp: "Hazard history & getting help",
    disasterHint:
      "Federally declared disasters for this county (FEMA record) — useful evidence when asking for local preparedness investment.",
    noDisasters: "✓ No federal disaster declarations on record for this county.",
    disastersUnavailable: "Federal disaster records are unavailable right now.",
    residentAid: "resident aid was available",
    recentBadge: "recent",
    whereToGetHelp: "Where to get help",
    officialOnly:
      "Official programs only — every link goes to a government or established nonprofit site. Disaster history: FEMA OpenFEMA.",
    languageBtn: "Español",
    invalidZip: "Please enter a 5-digit zip code.",
    translatedNote: "Report translated to Spanish by AI — verify important details.",
    newSearch: "New search",
  },
  es: {
    kicker: "✳ ESTUDIO AMBIENTAL DE CAMPO ✳",
    tagline: "Justicia ambiental por código postal",
    heroLead:
      "Cada vecindario tiene una historia ambiental. Ingrese un código postal para ver la calidad del aire, la carga de contaminación, los sitios industriales y las áreas verdes — convertidos en un reporte en lenguaje sencillo.",
    searchPlaceholder: "Ingrese un código postal de 5 dígitos",
    searchBtn: "Buscar",
    try: "Pruebe",
    recent: "Recientes",
    fact1: "Indicadores ambientales monitoreados",
    fact2: "Calificación en lenguaje sencillo para cada código",
    fact3: "Calidad del aire en tiempo real y alertas oficiales",
    zipCode: "Código postal",
    gradeLabel: "NOTA",
    outOf100: "de 100",
    shareReport: "Compartir este reporte",
    pinCompare: "Fijar para comparar",
    unpinCompare: "Quitar comparación",
    rightNowIn: "Ahora mismo en",
    liveConditions: "condiciones medidas en vivo",
    aqiLabel: "Índice de calidad del aire",
    outlook: "Pronóstico de 3 días (NWS)",
    noAlerts: "✓ No hay alertas meteorológicas activas para esta área en este momento.",
    whatGradeMeans: "Qué significa la calificación",
    whatYouCanDo: "Qué puede hacer usted",
    takeAction: "Tome acción",
    takeActionHint: "Convierta este reporte en algo que las autoridades tengan que responder.",
    copyLetter: "Copiar una carta para sus autoridades",
    letterCopied: "Carta copiada — péguela en un correo",
    reportViolation: "Reportar una violación a la EPA",
    findOfficials: "Encuentre a sus representantes electos",
    printReport: "Imprimir este reporte",
    actionNote:
      "La carta ya incluye los números reales de este reporte — péguela en un correo, agregue su nombre y envíela. Imprimir le da una copia limpia para llevar a una reunión del concejo o del vecindario.",
    hazardHelp: "Historial de desastres y cómo obtener ayuda",
    disasterHint:
      "Desastres declarados federalmente en este condado (registro de FEMA) — evidencia útil al pedir inversión local en preparación.",
    noDisasters: "✓ No hay declaraciones federales de desastre registradas para este condado.",
    disastersUnavailable: "Los registros federales de desastres no están disponibles en este momento.",
    residentAid: "hubo ayuda para residentes",
    recentBadge: "reciente",
    whereToGetHelp: "Dónde obtener ayuda",
    officialOnly:
      "Solo programas oficiales — cada enlace lleva a un sitio del gobierno o de una organización sin fines de lucro establecida. Historial de desastres: FEMA OpenFEMA.",
    languageBtn: "English",
    invalidZip: "Por favor ingrese un código postal de 5 dígitos.",
    translatedNote: "Reporte traducido al español por IA — verifique los detalles importantes.",
    newSearch: "Nueva búsqueda",
  },
};

export function makeT(lang) {
  const table = STRINGS[lang] ?? STRINGS.en;
  return (key) => table[key] ?? STRINGS.en[key] ?? key;
}
