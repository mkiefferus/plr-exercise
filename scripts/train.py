from __future__ import print_function
import argparse
import torch
import torch.nn.functional as F
import torch.optim as optim
from torchvision import datasets, transforms
from torch.optim.lr_scheduler import StepLR
import wandb
import optuna

from plr_exercise.models.cnn import Net

wandb.login()

wandb.init(project="plr-intro-exercise", entity="kiema745", name="Optuna optimisation")


def train(
    args,
    model: torch.nn.Module,
    device: torch.device,
    train_loader: torch.utils.data.DataLoader,
    optimizer: torch.optim.Optimizer,
    epoch: int,
):
    """
    Trains the model for a single epoch

    Parameters:
    - args: Argument Parser containing training settings
    - model: Neural net to be trained
    - device: Device (CPU or GPU) to run the model on
    - train_loader: Dataloader for the training dataset
    - optimizer: Optimisation algorithm used for training
    - epoch: Current epoch
    """
    model.train()
    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        output = model(data)
        loss = F.nll_loss(output, target)
        loss.backward()
        optimizer.step()
        if batch_idx % args.log_interval == 0:
            print(
                "Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}".format(
                    epoch,
                    batch_idx * len(data),
                    len(train_loader.dataset),
                    100.0 * batch_idx / len(train_loader),
                    loss.item(),
                )
            )
            if args.dry_run:
                break

        wandb.log({"training_loss": loss.item()})


def test(model, device, test_loader, epoch):
    """
    Evaluate model on test dataset

    Parameters:
    - model: Neural net to be evaluated
    - device: Device (CPU or GPU) to run the model on
    - test_loader: Dataloader for the test dataset
    - epoch: Current epoch

    Returns:
    - loss (float): Average loss on the test dataset
    """
    model.eval()
    test_loss = 0
    correct = 0

    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            test_loss += F.nll_loss(output, target, reduction="sum").item()  # sum up batch loss
            pred = output.argmax(dim=1, keepdim=True)  # get the index of the max log-probability
            correct += pred.eq(target.view_as(pred)).sum().item()

    test_loss /= len(test_loader.dataset)

    print(
        "\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)\n".format(
            test_loss, correct, len(test_loader.dataset), 100.0 * correct / len(test_loader.dataset)
        )
    )
    wandb.log({"test_loss": test_loss, "epoch": epoch})

    return test_loss


def train_model(trial, args, model, device):
    """ 
    Trains and evaluates the model using parameters from Optuna trial

    Parameters:
    - trial: Optuna trial object
    - args: Argument Parser containing training settings
    - model: Neural net to be trained and evaluated
    - device: Device (CPU or GPU) to run the model on
    
    Returns:
    -loss (float): Average evaluation loss of test dataset
    """
    lr = trial.suggest_float("lr", 1e-5, 1e-1, log=True)
    batch_size = trial.suggest_categorical("batch_size", [64, 128, 256])
    gamma = trial.suggest_float("gamma", 0.5, 0.9)

    use_cuda = not args.no_cuda and torch.cuda.is_available()

    train_kwargs = {"batch_size": batch_size}
    test_kwargs = {"batch_size": args.test_batch_size}
    if use_cuda:
        cuda_kwargs = {"num_workers": 1, "pin_memory": True, "shuffle": True}
        train_kwargs.update(cuda_kwargs)
        test_kwargs.update(cuda_kwargs)

    transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))])
    dataset1 = datasets.MNIST("../data", train=True, download=True, transform=transform)
    dataset2 = datasets.MNIST("../data", train=False, transform=transform)
    train_loader = torch.utils.data.DataLoader(dataset1, **train_kwargs)
    test_loader = torch.utils.data.DataLoader(dataset2, **test_kwargs)

    optimizer = optim.Adam(model.parameters(), lr=lr)

    scheduler = StepLR(optimizer, step_size=1, gamma=gamma)

    for epoch in range(args.epochs):
        train(args, model, device, train_loader, optimizer, epoch)
        loss = test(model, device, test_loader, epoch)
        scheduler.step()

    return loss


def main():
    """
    Execute training and hyperparameter tuning for model on MNIST dataset

    Parses command-line arguments for model training configuration
    """
    # Training settings
    parser = argparse.ArgumentParser(description="PyTorch MNIST Example")
    parser.add_argument(
        "--batch-size", type=int, default=64, metavar="N", help="input batch size for training (default: 64)"
    )
    parser.add_argument(
        "--test-batch-size", type=int, default=1000, metavar="N", help="input batch size for testing (default: 1000)"
    )
    parser.add_argument("--epochs", type=int, default=2, metavar="N", help="number of epochs to train (default: 14)")
    parser.add_argument("--lr", type=float, default=1.0, metavar="LR", help="learning rate (default: 1.0)")
    parser.add_argument("--gamma", type=float, default=0.7, metavar="M", help="Learning rate step gamma (default: 0.7)")
    parser.add_argument("--no-cuda", action="store_true", default=False, help="disables CUDA training")
    parser.add_argument("--dry-run", action="store_true", default=False, help="quickly check a single pass")
    parser.add_argument("--seed", type=int, default=1, metavar="S", help="random seed (default: 1)")
    parser.add_argument(
        "--log-interval",
        type=int,
        default=10,
        metavar="N",
        help="how many batches to wait before logging training status",
    )
    parser.add_argument("--save-model", action="store_true", default=False, help="For Saving the current Model")
    args = parser.parse_args()
    use_cuda = not args.no_cuda and torch.cuda.is_available()

    torch.manual_seed(args.seed)

    if use_cuda:
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")

    # use Optuna to optimise
    model = Net().to(device)

    study = optuna.create_study(direction="minimize")
    study.optimize(lambda trial: train_model(trial, args, model, device), n_trials=10)
    print(f"Best parameters: {study.best_params}")

    if args.save_model:
        torch.save(model.state_dict(), "mnist_cnn.pt")

        # log code artifact
        code_artifact = wandb.Artifact("training_script", type="code")
        code_artifact.add_file("scripts/train.py")
        wandb.log_artifact(code_artifact)


if __name__ == "__main__":
    main()
