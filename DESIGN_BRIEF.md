# Design brief — The Drone With a Nervous System

## Subject material
Name the real source, the Expanso edge node, and the destination.

## Palette rationale
The dark tokens are a starting point. Re-hue them for this subject.

## Scenario cast
Source sends data to the edge; the edge delivers to the destination.
Expanso Cloud deploys and receives status over control lanes only.

## Lane semantics
Source and destination lanes represent data rates from `/api/state`.
The two Cloud lanes represent deploy and status signals, never data.
