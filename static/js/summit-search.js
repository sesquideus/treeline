/*
 * Suggestions under a summit search box.
 *
 * The compare page used to offer two `<select>`s holding all 2410 summits. This replaces
 * them with what the admin does: type a few letters, pick from a short list. The box is a
 * plain text input carrying the name, so a name typed in full and submitted without ever
 * touching this file still works — the server resolves it either way, and nothing hidden
 * can fall out of step with what is on screen.
 */

const SEARCH_DELAY = 180;       // ms; long enough that typing a word is one request, not six
const SEARCH_MINIMUM = 2;       // matches SummitSearch.MINIMUM: one letter means nothing

function summitSearchBoxes(root = document) {
    return root.querySelectorAll('input[data-search-url]');
}

/* The list element for a box, created on first use and reused after. */
function suggestionList(input) {
    let list = input.nextElementSibling;
    if (!list || !list.classList.contains('suggestions')) {
        list = document.createElement('ul');
        list.className = 'suggestions';
        list.hidden = true;
        input.insertAdjacentElement('afterend', list);
    }
    return list;
}

function renderSuggestions(input, results) {
    const list = suggestionList(input);
    list.innerHTML = '';
    results.forEach(function (summit) {
        const item = document.createElement('li');
        item.tabIndex = -1;
        item.dataset.name = summit.name;
        item.innerHTML = '<span class="mountain"></span><span class="altitude"></span>';
        item.firstChild.textContent = summit.name;
        item.lastChild.textContent = summit.altitude.toFixed(1);
        list.appendChild(item);
    });
    list.hidden = !results.length;
}

function closeSuggestions(input) {
    suggestionList(input).hidden = true;
}

function chooseSuggestion(input, item) {
    input.value = item.dataset.name;
    closeSuggestions(input);
    input.focus();
}

/* Arrow keys walk the list, Enter takes the highlighted one, Escape gives up. */
function moveHighlight(list, step) {
    const items = [...list.children];
    if (!items.length) {
        return null;
    }
    const current = items.findIndex(item => item.classList.contains('highlighted'));
    const next = items[Math.min(Math.max(current + step, 0), items.length - 1)];
    items.forEach(item => item.classList.remove('highlighted'));
    next.classList.add('highlighted');
    return next;
}

function initSummitSearch(root = document) {
    summitSearchBoxes(root).forEach(function (input) {
        let timer = null;

        input.addEventListener('input', function () {
            clearTimeout(timer);
            const query = input.value.trim();
            if (query.length < SEARCH_MINIMUM) {
                closeSuggestions(input);
                return;
            }
            timer = setTimeout(function () {
                fetch(input.dataset.searchUrl + '?q=' + encodeURIComponent(query))
                    .then(response => response.json())
                    .then(data => renderSuggestions(input, data.results))
                    // A failed lookup leaves the box alone: it is still a usable text field.
                    .catch(() => closeSuggestions(input));
            }, SEARCH_DELAY);
        });

        input.addEventListener('keydown', function (event) {
            const list = suggestionList(input);
            if (event.key === 'Escape') {
                closeSuggestions(input);
            } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                if (!list.hidden) {
                    event.preventDefault();
                    moveHighlight(list, event.key === 'ArrowDown' ? 1 : -1);
                }
            } else if (event.key === 'Enter' && !list.hidden) {
                const highlighted = list.querySelector('.highlighted');
                if (highlighted) {
                    event.preventDefault();   // choose, rather than submitting half-typed
                    chooseSuggestion(input, highlighted);
                }
            }
        });

        suggestionList(input).addEventListener('mousedown', function (event) {
            // `mousedown`, not `click`: blur would hide the list before the click landed.
            const item = event.target.closest('li');
            if (item) {
                event.preventDefault();
                chooseSuggestion(input, item);
            }
        });

        input.addEventListener('blur', () => setTimeout(() => closeSuggestions(input), 120));
    });
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => initSummitSearch());
}
