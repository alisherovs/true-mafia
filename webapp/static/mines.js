let gameId = null;
let isPlaying = false;
let balance = 0;

function setMessage(text, type = 'info') {
  const el = document.getElementById('message');
  el.textContent = text;
  el.className = 'text-center text-sm min-h-[1.5rem] ' +
    (type === 'error' ? 'text-red-400' : type === 'success' ? 'text-green-400' : 'text-gray-400');
}

function renderGrid(cells) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  cells.forEach(c => {
    const div = document.createElement('div');
    div.className = 'mine-cell';
    div.dataset.index = c.index;
    if (c.opened) {
      if (c.is_mine) {
        div.classList.add('mine');
        div.textContent = '💣';
      } else {
        div.classList.add('safe');
        div.textContent = '💎';
      }
    } else if (c.is_mine === true && !isPlaying) {
      div.classList.add('revealed-mine');
      div.textContent = '💣';
    } else {
      div.classList.add('closed');
      div.onclick = () => openCell(c.index);
    }
    grid.appendChild(div);
  });
}

function renderEmptyGrid() {
  const cells = Array.from({ length: 36 }, (_, i) => ({ index: i, opened: false, is_mine: null }));
  renderGrid(cells);
}

async function startGame() {
  const betInput = document.getElementById('betInput');
  const bet = parseInt(betInput.value) || 0;
  setMessage('');

  const form = new FormData();
  form.append('bet', bet);

  try {
    const res = await fetch('/mines/start', { method: 'POST', body: form });
    const data = await res.json();
    if (!data.ok) {
      setMessage(data.error, 'error');
      return;
    }
    gameId = data.game_id;
    isPlaying = true;
    balance -= bet;
    updateBalance(balance);

    document.getElementById('startBtn').classList.add('hidden');
    document.getElementById('cashoutBtn').classList.remove('hidden');
    document.getElementById('infoBar').classList.remove('hidden');
    document.getElementById('openedCount').textContent = '0';
    document.getElementById('currentMult').textContent = '1.00';
    document.getElementById('cashoutAmount').textContent = '0';
    document.getElementById('multiplier').textContent = '1.00';
    betInput.disabled = true;

    renderGrid(data.cells);
    setMessage('O\'yin boshlandi! Katak tanlang.', 'info');
  } catch (e) {
    setMessage('Xatolik: ' + e.message, 'error');
  }
}

async function openCell(cell) {
  if (!isPlaying || !gameId) return;
  setMessage('');

  const form = new FormData();
  form.append('game_id', gameId);
  form.append('cell', cell);

  try {
    const res = await fetch('/mines/open', { method: 'POST', body: form });
    const data = await res.json();
    if (!data.ok) {
      setMessage(data.error, 'error');
      return;
    }

    if (data.result === 'mine') {
      isPlaying = false;
      const cells = Array.from({ length: 36 }, (_, i) => ({
        index: i,
        opened: i === cell || !data.mines.includes(i),
        is_mine: data.mines.includes(i),
      }));
      renderGrid(cells);
      setMessage('💣 Mina! Stavka kuyib ketdi.', 'error');
      endGame();
      return;
    }

    // Safe cell
    const opened = document.querySelectorAll('.mine-cell.safe').length + 1;
    const cells = Array.from(document.querySelectorAll('.mine-cell')).map((el, i) => ({
      index: i,
      opened: el.classList.contains('safe') || i === cell,
      is_mine: null,
    }));
    cells[cell].is_mine = false;
    renderGrid(cells);

    document.getElementById('openedCount').textContent = data.opened_count;
    document.getElementById('currentMult').textContent = data.multiplier.toFixed(2);
    document.getElementById('cashoutAmount').textContent = data.payout;
    document.getElementById('multiplier').textContent = data.multiplier.toFixed(2);
    setMessage(`💎 Safe! Koef: x${data.multiplier.toFixed(2)}`, 'success');
  } catch (e) {
    setMessage('Xatolik: ' + e.message, 'error');
  }
}

async function cashout() {
  if (!isPlaying || !gameId) return;
  setMessage('');

  const form = new FormData();
  form.append('game_id', gameId);

  try {
    const res = await fetch('/mines/cashout', { method: 'POST', body: form });
    const data = await res.json();
    if (!data.ok) {
      setMessage(data.error, 'error');
      return;
    }
    isPlaying = false;
    balance += data.payout;
    updateBalance(balance);

    setMessage(`🏆 Yutuq: ${data.payout}$ (x${data.multiplier.toFixed(2)})`, 'success');
    endGame();
  } catch (e) {
    setMessage('Xatolik: ' + e.message, 'error');
  }
}

function endGame() {
  document.getElementById('startBtn').classList.remove('hidden');
  document.getElementById('cashoutBtn').classList.add('hidden');
  document.getElementById('infoBar').classList.add('hidden');
  document.getElementById('betInput').disabled = false;
  gameId = null;
}

function updateBalance(val) {
  document.getElementById('balance').textContent = val + ' $';
}

function resumeGame(id, bet, openedCount, multiplier, payout, cells) {
  gameId = id;
  isPlaying = true;
  document.getElementById('startBtn').classList.add('hidden');
  document.getElementById('cashoutBtn').classList.remove('hidden');
  document.getElementById('infoBar').classList.remove('hidden');
  document.getElementById('betInput').value = bet;
  document.getElementById('betInput').disabled = true;
  document.getElementById('openedCount').textContent = openedCount;
  document.getElementById('currentMult').textContent = multiplier.toFixed(2);
  document.getElementById('cashoutAmount').textContent = payout;
  document.getElementById('multiplier').textContent = multiplier.toFixed(2);
  renderGrid(cells);
}

// Init empty grid on load
renderEmptyGrid();
