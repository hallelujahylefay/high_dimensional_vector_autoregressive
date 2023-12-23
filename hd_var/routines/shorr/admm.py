from hd_var.hosvd import hosvd
from hd_var.utils import minimize_matrix_input
from hd_var.operations import mode_fold, fast_ttm, mode_unfold
from hd_var.routines.mlr.utils import constructX
from hd_var.routines.shorr.diagonal_least_square import diag_lsq
from hd_var.routines.shorr.splitting_orthogonal_constraint import unbalanced_procruste
from hd_var.routines.shorr.penalization import lambda_optimal
import hd_var.routines.shorr.sparse_orthogonal_regression as sor
import hd_var.routines.shorr.losses as losses
from functools import partial
import jax.numpy as jnp
import jax.lax


def criterion(inps):
    prev_A, A, iter, _, _, _, _ = inps
    return (iter < 100) & (jnp.linalg.norm(prev_A - A) / jnp.linalg.norm(prev_A) > 0.05)


def admm_compute(A_init, ranks, y_ts, pen_l=None, pen_k=None, rhos=(1.0, 1.0, 1.0), criterion=criterion, iter_sor=100):
    """
    See Algorithm 2. in the paper.
    Compute the SHORR estimate.
    """
    # Computing the initial HOSVD decomposition
    A = A_init
    Us, G = hosvd(A, ranks)
    G_shape = G.shape
    U1, U2, U3 = Us
    print(1 / (jnp.linalg.norm(U1, ord=1) * jnp.linalg.norm(U2, ord=1) * jnp.linalg.norm(U3, ord=1)) * 0.1)
    Us = (U1, U2, U3)
    P = U3.shape[0]
    N = U1.shape[0]
    # Creating the lagged tensors.
    X_ts = constructX(y_ts, P)
    x_ts = jnp.moveaxis(X_ts.T, -1, 0)
    x_ts_bis = x_ts.reshape(x_ts.shape[0], -1)
    y_ts_reshaped = y_ts.T.reshape((-1))
    # Computing useful quantities
    T = y_ts.shape[1]
    if pen_l is None:
        pen_l = lambda_optimal(N, P, T, jnp.eye(N))  # assuming unit covariance
    if pen_k is None:
        pen_k = pen_l

    mode_unfold_p = partial(mode_unfold, mode=0, shape=G_shape)
    subroutine = partial(sor.subroutine, T=T, y=y_ts_reshaped, max_iter=iter_sor)
    fun_factor_U1 = partial(losses.factor_U1, T=T, N=N, x_ts_bis=x_ts_bis)
    fun_factor_U2 = partial(losses.factor_U2, r2=ranks[1], X_ts=X_ts)
    fun_factor_U3 = partial(losses.factor_U3, r3=ranks[2], X_ts=X_ts)
    loss_for_G = partial(losses.penalized_loss_G, y_ts=y_ts,
                         x_ts=x_ts, ranks=ranks)

    def iter_fun(inps):
        A, prev_A, n_iter, Us, G, Ds, Vs, Cs_flattened, pen_k = inps
        U1, U2, U3 = Us
        G_flattened_mode1 = mode_fold(G, 0)

        factor_U1 = fun_factor_U1(U2=U2, U3=U3, G_flattened_mode1=G_flattened_mode1)
        factor_U1 = factor_U1.reshape((-1, factor_U1.shape[-1]))
        U1 = subroutine(B=U1, X=factor_U1, pen_l=pen_l * jnp.linalg.norm(U2, ord=1) * jnp.linalg.norm(U3, ord=1),
                        pen_k=pen_k)
        jax.debug.print("U1={U1}", U1=U1)
        factor_U2 = fun_factor_U2(U1=U1, U3=U3, G_flattened_mode1=G_flattened_mode1)
        factor_U2 = factor_U2.reshape((-1, factor_U2.shape[-1]))
        U2 = subroutine(B=U2.T, X=factor_U2,
                        pen_l=pen_l * jnp.linalg.norm(U1, ord=1) * jnp.linalg.norm(U3, ord=1), pen_k=pen_k).T
        factor_U3 = fun_factor_U3(U1=U1, U2=U2, G_flattened_mode1=G_flattened_mode1)
        factor_U3 = factor_U3.reshape((-1, factor_U3.shape[-1]))
        U3 = subroutine(B=U3, X=factor_U3, pen_l=pen_l * jnp.linalg.norm(U1, ord=1) * jnp.linalg.norm(U2, ord=1),
                        pen_k=pen_k)
        Us = (U1, U2, U3)
        A = fast_ttm(G, Us)
        return A, prev_A, n_iter + 1, Us, G, Ds, Vs, Cs_flattened, 2 * pen_k

    Ds = (jnp.eye(ranks[0]), jnp.eye(ranks[1]), jnp.eye(ranks[2]))
    Vs = (jnp.eye(ranks[1] * ranks[2], ranks[0]), jnp.eye(ranks[0] * ranks[2], ranks[1]),
          jnp.eye(ranks[0] * ranks[1], ranks[2]))
    Cs_flattened = (jnp.ones((ranks[0], ranks[1] * ranks[2])), jnp.ones((ranks[1], ranks[0] * ranks[2])),
                    jnp.ones((ranks[2], ranks[0] * ranks[1])))

    inps = (A, jnp.zeros_like(A), 0, Us, G, Ds, Vs, Cs_flattened, pen_k)
    A, *_ = jax.lax.while_loop(criterion, iter_fun, inps)
    Us, G = hosvd(A, ranks)
    return G, A, Us
