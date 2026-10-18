// Game hub page: lists the games from /api/games; picking one asks the hub to start that
// game's server (stopping the other) and then opens it. Keys: 1-4 pick a game.

const gamesEl = document.querySelector('#games');
const loading = document.querySelector('#loading');
const hint = document.querySelector('#hint');
let names = [];

async function play(name, title) {
  document.querySelector('#loading-title').textContent = `STARTING ${title.toUpperCase()}`;
  loading.hidden = false;
  try {
    const response = await fetch(`/api/play/${name}`, { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error ?? response.statusText);
    location.href = result.url;
  } catch (error) {
    loading.hidden = true;
    hint.textContent = `COULD NOT START ${title.toUpperCase()}: ${error.message}`;
  }
}

function card(name, game, index, running) {
  const link = document.createElement('a');
  link.className = 'hub-card';
  link.href = '#';
  link.style.setProperty('--accent', game.accent);
  link.innerHTML = `
    <img src="/${game.image}" alt="${game.title}" />
    <div class="hub-card-body">
      <div class="hub-key">${index + 1}</div>
      ${name === running ? '<div class="hub-running">RUNNING</div>' : ''}
      <h2></h2>
      <p class="hub-tagline"></p>
      <p class="hub-detail"></p>
    </div>`;
  link.querySelector('h2').textContent = game.title.toUpperCase();
  link.querySelector('.hub-tagline').textContent = game.tagline;
  link.querySelector('.hub-detail').textContent = game.detail;
  link.addEventListener('click', (event) => {
    event.preventDefault();
    play(name, game.title);
  });
  return link;
}

fetch('/api/games')
  .then((response) => response.json())
  .then(({ games, running }) => {
    names = Object.keys(games);
    names.forEach((name, index) => gamesEl.append(card(name, games[name], index, running)));
  })
  .catch(() => {
    hint.textContent = 'THE HUB SERVER IS NOT RESPONDING';
  });

window.addEventListener('keydown', (event) => {
  const name = names[Number(event.key) - 1];
  if (name && loading.hidden) gamesEl.children[Number(event.key) - 1].click();
});
