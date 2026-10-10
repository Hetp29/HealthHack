"""Neural CDE and Neural ODE encoders, using torchcde 0.2.5 / torchdiffeq.

A Neural CDE integrates `dz/dt = f_theta(z) dX/dt` ALONG the observed data path X --
the path "controls" the state evolution. A Neural ODE integrates `dz/dt = f_theta(z)`
from an initial condition and is NOT driven by the observations after initialisation.
That distinction is the point of comparing them here, and both are implemented so the
comparison is like for like (same hidden size, same vector-field depth, same head).

CAUSALITY OF THE INTERPOLATION
------------------------------
torchcde offers natural cubic spline coefficients, which are explicitly NON-CAUSAL:
they smooth across the whole path, so the value at time s depends on observations after
s. That is avoided here. Only

  * `linear_interpolation_coeffs`           -- piecewise linear, and
  * rectilinear (hold-then-step) coefficients

are used. Note that every observation in this window already precedes the meal
timestamp, so nothing could cross the forecast boundary regardless; the restriction is
about not letting the path at an interior time depend on later interior observations.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class VectorField(nn.Module):
    """f_theta: z -> dz/dX, shape (hidden, input_channels). Shallow by design."""

    def __init__(self, hidden: int, channels: int, width: int = 32):
        super().__init__()
        self.hidden, self.channels = hidden, channels
        self.net = nn.Sequential(
            nn.Linear(hidden, width), nn.Tanh(), nn.Linear(width, hidden * channels), nn.Tanh()
        )

    def forward(self, t, z):
        return self.net(z).view(z.shape[0], self.hidden, self.channels)


class OdeField(nn.Module):
    """f_theta: z -> dz/dt for the Neural ODE control (no data driving term)."""

    def __init__(self, hidden: int, width: int = 32):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(hidden, width), nn.Tanh(), nn.Linear(width, hidden), nn.Tanh()
        )

    def forward(self, t, z):
        return self.net(z)


class NeuralCde(nn.Module):
    """F1 -- Neural CDE encoder over the pre-meal path.

    `interpolation` selects the causal path construction: "linear" or "rectilinear".
    """

    def __init__(self, channels: int = 4, hidden: int = 8, interpolation: str = "linear",
                 width: int = 32, rtol: float = 1e-3, atol: float = 1e-4):
        super().__init__()
        self.channels, self.hidden = channels, hidden
        self.interpolation = interpolation
        self.initial = nn.Linear(channels, hidden)
        self.field = VectorField(hidden, channels, width)
        self.out_dim = hidden
        self.rtol, self.atol = rtol, atol
        self.nfe = 0

    def coeffs(self, path: torch.Tensor) -> torch.Tensor:
        """Path is (batch, length, channels) with time as channel 0."""
        import torchcde

        if self.interpolation == "rectilinear":
            return torchcde.linear_interpolation_coeffs(path, rectilinear=0)
        return torchcde.linear_interpolation_coeffs(path)

    def forward(self, path: torch.Tensor) -> torch.Tensor:
        import torchcde

        coeffs = self.coeffs(path)
        X = torchcde.LinearInterpolation(coeffs)
        z0 = self.initial(X.evaluate(X.interval[0]))
        z = torchcde.cdeint(
            X=X, func=self.field, z0=z0, t=X.interval,
            adjoint=False, method="rk4", options={"step_size": 1.0},
        )
        return z[:, -1]


class NeuralOde(nn.Module):
    """F6 -- Neural ODE control: state initialised from the path, then evolved freely."""

    def __init__(self, channels: int = 4, hidden: int = 8, width: int = 32):
        super().__init__()
        self.initial = nn.Linear(channels * 2, hidden)
        self.field = OdeField(hidden, width)
        self.out_dim = hidden

    def forward(self, path: torch.Tensor) -> torch.Tensor:
        from torchdiffeq import odeint

        summary = torch.cat([path[:, -1, :], path.mean(dim=1)], dim=-1)
        z0 = self.initial(summary)
        t = torch.tensor([0.0, 1.0], dtype=path.dtype)
        z = odeint(self.field, z0, t, method="rk4", options={"step_size": 0.25})
        return z[-1]


class PathModel(nn.Module):
    """Encoder over the path, plus static features, predicting four horizon deltas."""

    def __init__(self, encoder: nn.Module, static_dim: int, hidden: int = 32):
        super().__init__()
        self.encoder = encoder
        self.head = nn.Sequential(
            nn.Linear(encoder.out_dim + static_dim, hidden), nn.ReLU(),
            nn.Dropout(0.1), nn.Linear(hidden, 4),
        )

    def forward(self, path, static):
        return self.head(torch.cat([self.encoder(path), static], dim=-1))
