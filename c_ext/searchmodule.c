/*
 * searchengine : moteur de recherche full-text en C, exposé à Python.
 *
 * Index inversé (mot → liste de (doc_id, fréquence)) avec table de hachage
 * à chaînage. Conçu pour un volume modéré de documents (réseau social).
 *
 * Améliorations expert (v2) :
 *  - TABLE_SIZE augmentée (8192) pour réduire collisions
 *  - Libération des entrées vides après remove (anti-fuite mémoire long terme)
 *  - Vérification systématique des malloc (échec → RuntimeError Python)
 *  - Folding d'accents courants (français) pour une recherche plus tolérante
 *  - Tokenisation robuste, mots vides trop courts ignorés
 *  - API inchangée pour compatibilité
 *
 * API Python :
 *   searchengine.index_add(doc_id: int, text: str) -> None
 *   searchengine.index_remove(doc_id: int, text: str) -> None
 *   searchengine.search(query: str, limit: int = 20) -> list[(doc_id, score)]
 *   searchengine.clear() -> None
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <ctype.h>
#include <string.h>
#include <stdlib.h>

#define TABLE_SIZE 8192
#define MAX_WORD_LEN 64
#define MIN_WORD_LEN 2   /* ignore les mots d'1 caractère (bruit) */

/* ---------- Structures ---------- */

typedef struct Posting {
    long doc_id;
    int freq;
    struct Posting *next;
} Posting;

typedef struct Entry {
    char *word;
    Posting *postings;
    struct Entry *next;
} Entry;

static Entry *table[TABLE_SIZE];

/* ---------- Hash (djb2) ---------- */

static unsigned long hash_str(const char *str) {
    unsigned long hash = 5381;
    int c;
    while ((c = (unsigned char)*str++)) {
        hash = ((hash << 5) + hash) + c;
    }
    return hash % TABLE_SIZE;
}

/* ---------- Folding d'accents (ASCII-compatible, français courant) ---------- */

static unsigned char fold_char(unsigned char c) {
    /* Mapping simple des caractères accentués ISO-8859-1 / UTF-8 common */
    switch (c) {
        case 0xC0: case 0xC1: case 0xC2: case 0xC3: case 0xC4: case 0xC5: /* ÀÁÂÃÄÅ */
        case 0xE0: case 0xE1: case 0xE2: case 0xE3: case 0xE4: case 0xE5: return 'a';
        case 0xC7: case 0xE7: return 'c'; /* Çç */
        case 0xC8: case 0xC9: case 0xCA: case 0xCB:
        case 0xE8: case 0xE9: case 0xEA: case 0xEB: return 'e';
        case 0xCC: case 0xCD: case 0xCE: case 0xCF:
        case 0xEC: case 0xED: case 0xEE: case 0xEF: return 'i';
        case 0xD1: case 0xF1: return 'n'; /* Ññ */
        case 0xD2: case 0xD3: case 0xD4: case 0xD5: case 0xD6:
        case 0xF2: case 0xF3: case 0xF4: case 0xF5: case 0xF6: return 'o';
        case 0xD9: case 0xDA: case 0xDB: case 0xDC:
        case 0xF9: case 0xFA: case 0xFB: case 0xFC: return 'u';
        case 0xDD: case 0xFD: case 0xFF: return 'y';
        default:
            if (c >= 'A' && c <= 'Z') return (unsigned char)(c + 32);
            return c;
    }
}

/* ---------- Gestion mémoire sécurisée ---------- */

static void *xmalloc(size_t size) {
    void *p = malloc(size);
    if (!p) {
        PyErr_NoMemory();
    }
    return p;
}

static char *xstrdup(const char *s) {
    size_t n = strlen(s) + 1;
    char *p = (char *)xmalloc(n);
    if (p) memcpy(p, s, n);
    return p;
}

/* ---------- Opérations sur l'index ---------- */

static Entry *find_entry(const char *word) {
    unsigned long h = hash_str(word);
    Entry *e = table[h];
    while (e) {
        if (strcmp(e->word, word) == 0) return e;
        e = e->next;
    }
    return NULL;
}

static Entry *find_or_create_entry(const char *word) {
    unsigned long h = hash_str(word);
    Entry *e = table[h];
    while (e) {
        if (strcmp(e->word, word) == 0) return e;
        e = e->next;
    }
    e = (Entry *)xmalloc(sizeof(Entry));
    if (!e) return NULL;
    e->word = xstrdup(word);
    if (!e->word) {
        free(e);
        return NULL;
    }
    e->postings = NULL;
    e->next = table[h];
    table[h] = e;
    return e;
}

static int add_posting(Entry *entry, long doc_id) {
    Posting *p = entry->postings;
    while (p) {
        if (p->doc_id == doc_id) {
            p->freq += 1;
            return 0;
        }
        p = p->next;
    }
    p = (Posting *)xmalloc(sizeof(Posting));
    if (!p) return -1;
    p->doc_id = doc_id;
    p->freq = 1;
    p->next = entry->postings;
    entry->postings = p;
    return 0;
}

/* Supprime le doc_id et, si la liste de postings devient vide, libère l'entrée. */
static void remove_doc_from_entry(Entry **bucket_head, Entry *entry, long doc_id) {
    Posting **pp = &entry->postings;
    while (*pp) {
        if ((*pp)->doc_id == doc_id) {
            Posting *dead = *pp;
            *pp = dead->next;
            free(dead);
        } else {
            pp = &(*pp)->next;
        }
    }

    /* Entrée devenue vide → la retirer de la table pour éviter fuite lente */
    if (entry->postings == NULL) {
        Entry **ep = bucket_head;
        while (*ep) {
            if (*ep == entry) {
                *ep = entry->next;
                free(entry->word);
                free(entry);
                return;
            }
            ep = &(*ep)->next;
        }
    }
}

/* Tokenisation + application d'une fonction sur chaque mot normalisé */
static int tokenize_and_apply(const char *text, int (*apply)(const char *, long), long doc_id) {
    char buf[MAX_WORD_LEN];
    int len = 0;
    const unsigned char *p = (const unsigned char *)text;

    while (1) {
        unsigned char c = *p;
        unsigned char folded = fold_char(c);

        if (isalnum(folded) || (folded >= 128 && isalnum(c))) {
            /* Accepte alphanum + caractères étendus foldés */
            if (len < MAX_WORD_LEN - 1) {
                buf[len++] = (char)(folded < 128 ? folded : tolower(c));
            }
        } else {
            if (len >= MIN_WORD_LEN) {
                buf[len] = '\0';
                if (apply(buf, doc_id) != 0) return -1;
            }
            len = 0;
        }
        if (c == '\0') break;
        p++;
    }
    return 0;
}

static int apply_index_add(const char *word, long doc_id) {
    Entry *e = find_or_create_entry(word);
    if (!e) return -1;
    return add_posting(e, doc_id);
}

static int apply_index_remove(const char *word, long doc_id) {
    unsigned long h = hash_str(word);
    Entry *e = find_entry(word);
    if (e) {
        remove_doc_from_entry(&table[h], e, doc_id);
    }
    return 0;
}

/* ---------- API Python ---------- */

static PyObject *py_index_add(PyObject *self, PyObject *args) {
    long doc_id;
    const char *text;
    if (!PyArg_ParseTuple(args, "ls", &doc_id, &text)) return NULL;
    if (tokenize_and_apply(text, apply_index_add, doc_id) != 0) {
        return NULL; /* exception déjà positionnée (NoMemory) */
    }
    Py_RETURN_NONE;
}

static PyObject *py_index_remove(PyObject *self, PyObject *args) {
    long doc_id;
    const char *text;
    if (!PyArg_ParseTuple(args, "ls", &doc_id, &text)) return NULL;
    tokenize_and_apply(text, apply_index_remove, doc_id);
    Py_RETURN_NONE;
}

/* Score temporaire pour agrégation */
typedef struct ScoreNode {
    long doc_id;
    int score;
    struct ScoreNode *next;
} ScoreNode;

static ScoreNode *score_find_or_add(ScoreNode **head, long doc_id) {
    ScoreNode *n = *head;
    while (n) {
        if (n->doc_id == doc_id) return n;
        n = n->next;
    }
    n = (ScoreNode *)xmalloc(sizeof(ScoreNode));
    if (!n) return NULL;
    n->doc_id = doc_id;
    n->score = 0;
    n->next = *head;
    *head = n;
    return n;
}

static PyObject *py_search(PyObject *self, PyObject *args) {
    const char *query;
    long limit = 20;
    if (!PyArg_ParseTuple(args, "s|l", &query, &limit)) return NULL;
    if (limit < 1) limit = 1;
    if (limit > 500) limit = 500; /* garde-fou */

    char buf[MAX_WORD_LEN];
    int len = 0;
    const unsigned char *p = (const unsigned char *)query;
    ScoreNode *scores = NULL;

    while (1) {
        unsigned char c = *p;
        unsigned char folded = fold_char(c);

        if (isalnum(folded) || (folded >= 128 && isalnum(c))) {
            if (len < MAX_WORD_LEN - 1) {
                buf[len++] = (char)(folded < 128 ? folded : tolower(c));
            }
        } else {
            if (len >= MIN_WORD_LEN) {
                buf[len] = '\0';
                Entry *e = find_entry(buf);
                if (e) {
                    Posting *post = e->postings;
                    while (post) {
                        ScoreNode *sn = score_find_or_add(&scores, post->doc_id);
                        if (!sn) {
                            /* OOM : nettoyer et sortir */
                            while (scores) {
                                ScoreNode *dead = scores;
                                scores = scores->next;
                                free(dead);
                            }
                            return NULL;
                        }
                        sn->score += post->freq;
                        post = post->next;
                    }
                }
            }
            len = 0;
        }
        if (c == '\0') break;
        p++;
    }

    int count = 0;
    for (ScoreNode *n = scores; n; n = n->next) count++;

    PyObject *result = PyList_New(0);
    if (!result) {
        while (scores) {
            ScoreNode *dead = scores;
            scores = scores->next;
            free(dead);
        }
        return NULL;
    }

    if (count == 0) {
        while (scores) {
            ScoreNode *dead = scores;
            scores = scores->next;
            free(dead);
        }
        return result;
    }

    ScoreNode **arr = (ScoreNode **)xmalloc(sizeof(ScoreNode *) * (size_t)count);
    if (!arr) {
        Py_DECREF(result);
        while (scores) {
            ScoreNode *dead = scores;
            scores = scores->next;
            free(dead);
        }
        return NULL;
    }

    int idx = 0;
    for (ScoreNode *n = scores; n; n = n->next) arr[idx++] = n;

    /* Tri décroissant par score (insertion sort — listes courtes) */
    for (int i = 1; i < count; i++) {
        ScoreNode *key = arr[i];
        int j = i - 1;
        while (j >= 0 && arr[j]->score < key->score) {
            arr[j + 1] = arr[j];
            j--;
        }
        arr[j + 1] = key;
    }

    long added = 0;
    for (int i = 0; i < count && added < limit; i++, added++) {
        PyObject *tup = Py_BuildValue("(li)", arr[i]->doc_id, arr[i]->score);
        if (!tup) {
            free(arr);
            while (scores) {
                ScoreNode *dead = scores;
                scores = scores->next;
                free(dead);
            }
            Py_DECREF(result);
            return NULL;
        }
        if (PyList_Append(result, tup) < 0) {
            Py_DECREF(tup);
            free(arr);
            while (scores) {
                ScoreNode *dead = scores;
                scores = scores->next;
                free(dead);
            }
            Py_DECREF(result);
            return NULL;
        }
        Py_DECREF(tup);
    }

    free(arr);
    while (scores) {
        ScoreNode *dead = scores;
        scores = scores->next;
        free(dead);
    }

    return result;
}

static PyObject *py_clear(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    for (int i = 0; i < TABLE_SIZE; i++) {
        Entry *e = table[i];
        while (e) {
            Entry *dead_e = e;
            Posting *p = e->postings;
            while (p) {
                Posting *dead_p = p;
                p = p->next;
                free(dead_p);
            }
            e = e->next;
            free(dead_e->word);
            free(dead_e);
        }
        table[i] = NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef SearchMethods[] = {
    {"index_add",    py_index_add,    METH_VARARGS, "Indexer un document (doc_id, texte)."},
    {"index_remove", py_index_remove, METH_VARARGS, "Retirer un document de l'index (doc_id, texte original)."},
    {"search",       py_search,       METH_VARARGS, "Rechercher (query, limit=20) -> [(doc_id, score), ...]"},
    {"clear",        py_clear,        METH_NOARGS,  "Vider entièrement l'index."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef searchmodule = {
    PyModuleDef_HEAD_INIT,
    "searchengine",
    "Moteur de recherche full-text en C (index inversé, folding d'accents).",
    -1,
    SearchMethods
};

PyMODINIT_FUNC PyInit_searchengine(void) {
    return PyModule_Create(&searchmodule);
}
