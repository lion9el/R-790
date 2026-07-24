/*
 * searchengine : moteur de recherche full-text en C, exposé à Python.
 *
 * Implémente un index inversé (mot -> liste de doc_id) avec une table de
 * hachage maison. Utilisé par le backend pour indexer les posts et
 * répondre aux recherches par mot-clé.
 *
 * API Python exposée :
 *   searchengine.index_add(doc_id: int, text: str) -> None
 *   searchengine.index_remove(doc_id: int) -> None
 *   searchengine.search(query: str, limit: int = 20) -> list[(doc_id, score)]
 *   searchengine.clear() -> None
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <ctype.h>
#include <string.h>
#include <stdlib.h>

#define TABLE_SIZE 4096
#define MAX_WORD_LEN 64

/* Une occurrence d'un mot dans un document, avec son nombre d'occurrences */
typedef struct Posting {
    long doc_id;
    int freq;
    struct Posting *next;
} Posting;

/* Une entrée de la table de hachage : un mot -> sa liste de postings */
typedef struct Entry {
    char *word;
    Posting *postings;
    struct Entry *next; /* chaînage pour collisions */
} Entry;

static Entry *table[TABLE_SIZE];

/* djb2 */
static unsigned long hash_str(const char *str) {
    unsigned long hash = 5381;
    int c;
    while ((c = (unsigned char)*str++)) {
        hash = ((hash << 5) + hash) + c;
    }
    return hash % TABLE_SIZE;
}

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
    e = (Entry *)malloc(sizeof(Entry));
    e->word = strdup(word);
    e->postings = NULL;
    e->next = table[h];
    table[h] = e;
    return e;
}

static void add_posting(Entry *entry, long doc_id) {
    Posting *p = entry->postings;
    while (p) {
        if (p->doc_id == doc_id) {
            p->freq += 1;
            return;
        }
        p = p->next;
    }
    p = (Posting *)malloc(sizeof(Posting));
    p->doc_id = doc_id;
    p->freq = 1;
    p->next = entry->postings;
    entry->postings = p;
}

static void remove_doc_from_entry(Entry *entry, long doc_id) {
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
}

/* Découpe text en mots (lettres/chiffres uniquement), en minuscules */
static void tokenize_and_apply(const char *text, void (*apply)(const char *, long), long doc_id) {
    char buf[MAX_WORD_LEN];
    int len = 0;
    const char *p = text;
    while (1) {
        char c = *p;
        if (isalnum((unsigned char)c)) {
            if (len < MAX_WORD_LEN - 1) {
                buf[len++] = (char)tolower((unsigned char)c);
            }
        } else {
            if (len > 0) {
                buf[len] = '\0';
                apply(buf, doc_id);
                len = 0;
            }
        }
        if (c == '\0') break;
        p++;
    }
}

static void apply_index_add(const char *word, long doc_id) {
    Entry *e = find_or_create_entry(word);
    add_posting(e, doc_id);
}

static void apply_index_remove(const char *word, long doc_id) {
    Entry *e = find_entry(word);
    if (e) remove_doc_from_entry(e, doc_id);
}

/* ---------- Fonctions Python ---------- */

static PyObject *py_index_add(PyObject *self, PyObject *args) {
    long doc_id;
    const char *text;
    if (!PyArg_ParseTuple(args, "ls", &doc_id, &text)) return NULL;
    tokenize_and_apply(text, apply_index_add, doc_id);
    Py_RETURN_NONE;
}

static PyObject *py_index_remove(PyObject *self, PyObject *args) {
    long doc_id;
    const char *text;
    if (!PyArg_ParseTuple(args, "ls", &doc_id, &text)) return NULL;
    tokenize_and_apply(text, apply_index_remove, doc_id);
    Py_RETURN_NONE;
}

/* Structure de score temporaire, utilisée pour agréger et trier les résultats */
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
    n = (ScoreNode *)malloc(sizeof(ScoreNode));
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

    char buf[MAX_WORD_LEN];
    int len = 0;
    const char *p = query;
    ScoreNode *scores = NULL;

    while (1) {
        char c = *p;
        if (isalnum((unsigned char)c)) {
            if (len < MAX_WORD_LEN - 1) buf[len++] = (char)tolower((unsigned char)c);
        } else {
            if (len > 0) {
                buf[len] = '\0';
                Entry *e = find_entry(buf);
                if (e) {
                    Posting *post = e->postings;
                    while (post) {
                        ScoreNode *sn = score_find_or_add(&scores, post->doc_id);
                        sn->score += post->freq;
                        post = post->next;
                    }
                }
                len = 0;
            }
        }
        if (c == '\0') break;
        p++;
    }

    /* Compter et trier (tri par insertion, listes courtes attendues) */
    int count = 0;
    for (ScoreNode *n = scores; n; n = n->next) count++;

    PyObject *result = PyList_New(0);

    /* Copier dans un tableau pour trier facilement */
    ScoreNode **arr = (ScoreNode **)malloc(sizeof(ScoreNode *) * (count > 0 ? count : 1));
    int idx = 0;
    for (ScoreNode *n = scores; n; n = n->next) arr[idx++] = n;

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
        PyList_Append(result, tup);
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
    {"index_add", py_index_add, METH_VARARGS, "Indexer un document (doc_id, texte)."},
    {"index_remove", py_index_remove, METH_VARARGS, "Retirer un document de l'index (doc_id, texte original)."},
    {"search", py_search, METH_VARARGS, "Rechercher (query, limit=20) -> [(doc_id, score), ...]"},
    {"clear", py_clear, METH_NOARGS, "Vider entièrement l'index."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef searchmodule = {
    PyModuleDef_HEAD_INIT,
    "searchengine",
    "Moteur de recherche full-text en C (index inversé).",
    -1,
    SearchMethods
};

PyMODINIT_FUNC PyInit_searchengine(void) {
    return PyModule_Create(&searchmodule);
}
