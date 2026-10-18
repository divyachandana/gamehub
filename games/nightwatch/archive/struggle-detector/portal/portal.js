/**
 * Portal behavior. The validation is strict and the messages are vague on purpose:
 * that is what real patients run into, and it is what the struggle detector must catch.
 */
import { Tracker } from './tracker.js';

const $ = (id) => document.getElementById(id);
let step = 1;

const tracker = new Tracker((reply) => {
  if (reply.help) showHelp(reply.help);
});

// Strict checks with unhelpful error text (the help messages explain them properly).
const CHECKS = {
  1: () => (/^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-(19|20)\d{2}$/.test($('dob').value.trim())
    ? null : ['dob', 'dob_format', 'Invalid input.']),
  2: () => {
    if (!/^RX\d{7}$/.test($('rx').value.trim())) return ['rx', 'rx_number', 'Entry not recognized.'];
    const p = $('pharmacy').value;
    if (!p || (p === 'other' && !$('other').value.trim())) return ['pharmacy', 'pharmacy', 'Selection required.'];
    return null;
  },
  3: () => ($('consent').checked ? null : ['consent', 'consent', 'Unable to proceed (code 4021).']),
};

function show(screen) {
  for (const id of ['intro', 'task', 'done']) $(id).classList.toggle('hidden', id !== screen);
}

function goTo(next, wentBack = false) {
  step = next;
  document.querySelectorAll('.step').forEach((el) => el.classList.toggle('hidden', +el.dataset.step !== step));
  document.querySelectorAll('.steps span').forEach((el) => el.classList.toggle('on', +el.dataset.s === step));
  document.querySelectorAll('.err').forEach((el) => { el.textContent = ''; });
  $('next').textContent = step === 3 ? 'submit' : 'continue';
  tracker.stepChanged(step, wentBack);
}

function clearHighlights() {
  document.querySelectorAll('.highlight').forEach((el) => el.classList.remove('highlight'));
}

function showHelp({ message, target }) {
  $('helper-text').textContent = message;
  $('helper').classList.remove('hidden');
  clearHighlights();
  const el = target && $(target);
  if (el) {
    el.classList.add('highlight');
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

async function finish(completed) {
  const { duration_s: seconds } = await tracker.end(completed);
  $('helper').classList.add('hidden');
  clearHighlights();
  $('done-title').textContent = completed ? 'Refill requested' : 'Task ended';
  $('done-text').textContent = completed
    ? `Done in ${seconds} s. Your pharmacy will text you when it's ready.`
    : `Stopped after ${seconds} s. That's useful data too, thank you.`;
  show('done');
}

$('begin').addEventListener('click', async () => {
  $('form').reset();
  $('other-wrap').classList.add('hidden');
  show('task');
  await tracker.start($('assist').checked);
  goTo(1);
});

$('next').addEventListener('click', () => {
  const problem = CHECKS[step]();
  if (problem) {
    const [, errorId, text] = problem;
    const box = $(`${problem[0]}-err`);
    box.textContent = text;
    tracker.error(errorId);
    return;
  }
  if (step === 3) return finish(true);
  goTo(step + 1);
});

$('back').addEventListener('click', () => {
  if (step > 1) goTo(step - 1, true);
  else tracker.error('back_on_first');
});

$('pharmacy').addEventListener('change', () => {
  $('other-wrap').classList.toggle('hidden', $('pharmacy').value !== 'other');
});

$('giveup').addEventListener('click', () => finish(false));
$('helper-ok').addEventListener('click', () => { $('helper').classList.add('hidden'); clearHighlights(); });
$('again').addEventListener('click', () => show('intro'));
