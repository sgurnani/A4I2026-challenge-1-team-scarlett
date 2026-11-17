ASSERT (SELECT COUNT(*) = COUNT(DISTINCT node_id) FROM `{{PROJECT}}.{{WORK}}.routing_nodes`)
  AS 'Routing node identifiers must be unique';
ASSERT (SELECT COUNT(*) = COUNT(DISTINCT edge_id) FROM `{{PROJECT}}.{{WORK}}.routing_edges`)
  AS 'Routing segment identifiers must be unique';
ASSERT (SELECT COUNTIF(length_m <= 0 OR IS_NAN(length_m) OR IS_INF(length_m)) = 0
  FROM `{{PROJECT}}.{{WORK}}.routing_edges`) AS 'Route distances must be positive and finite';
ASSERT (SELECT COUNT(*) = 0 FROM `{{PROJECT}}.{{WORK}}.routing_edges` e
  LEFT JOIN `{{PROJECT}}.{{WORK}}.routing_nodes` a ON a.node_id = e.from_node
  LEFT JOIN `{{PROJECT}}.{{WORK}}.routing_nodes` b ON b.node_id = e.to_node
  WHERE a.node_id IS NULL OR b.node_id IS NULL) AS 'Every segment endpoint must exist';

SELECT (SELECT COUNT(*) FROM `{{PROJECT}}.{{WORK}}.routing_nodes`) AS network_nodes,
  COUNT(*) AS network_segments,
  COUNTIF(walk_forward OR walk_backward) AS walking_segments,
  COUNTIF(truck_forward OR truck_backward) AS truck_segments,
  (SELECT COUNT(*) FROM `{{PROJECT}}.{{WORK}}.routing_entrances`) AS mapped_entrances,
  (SELECT COUNT(*) FROM `{{PROJECT}}.{{WORK}}.routing_turn_restrictions`) AS turn_restrictions
FROM `{{PROJECT}}.{{WORK}}.routing_edges`;
