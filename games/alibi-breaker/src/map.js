// Road graph + shortest drive times (Dijkstra). The map is plain data in the
// case file, so a real map (e.g. SF street data) can replace it later as long
// as it produces the same { places, roads } shape.

export function buildGraph(map) {
  const graph = new Map(map.places.map((p) => [p.id, []]));
  for (const road of map.roads) {
    graph.get(road.from).push({ to: road.to, minutes: road.minutes, name: road.name });
    graph.get(road.to).push({ to: road.from, minutes: road.minutes, name: road.name });
  }
  return { adjacency: graph, cache: new Map() };
}

export function shortestPath(graph, from, to) {
  const key = `${from}>${to}`;
  if (graph.cache.has(key)) return graph.cache.get(key);
  if (from === to) return { minutes: 0, path: [from], roads: [] };

  const dist = new Map([[from, 0]]);
  const prev = new Map();
  const queue = new Set(graph.adjacency.keys());
  while (queue.size) {
    let current = null;
    for (const id of queue) {
      if (dist.has(id) && (current === null || dist.get(id) < dist.get(current))) current = id;
    }
    if (current === null || current === to) break;
    queue.delete(current);
    for (const edge of graph.adjacency.get(current)) {
      const candidate = dist.get(current) + edge.minutes;
      if (candidate < (dist.get(edge.to) ?? Infinity)) {
        dist.set(edge.to, candidate);
        prev.set(edge.to, { id: current, road: edge.name });
      }
    }
  }
  if (!dist.has(to)) return null;

  const path = [to];
  const roads = [];
  while (path[0] !== from) {
    const step = prev.get(path[0]);
    roads.unshift(step.road);
    path.unshift(step.id);
  }
  const result = { minutes: dist.get(to), path, roads: [...new Set(roads)] };
  graph.cache.set(key, result);
  return result;
}
