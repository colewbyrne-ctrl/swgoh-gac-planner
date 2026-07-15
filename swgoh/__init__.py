"""SWGOH GAC strategy planner.

Scrapes SWGOH.GG Grand Arena data, solves a constrained counter-assignment
problem (beam search) to build an offensive plan, and ranks the defensive
teams that remain from the unused roster.
"""

__version__ = "0.1.0"
