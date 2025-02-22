from libauc.losses import AUCM_MultiLabel, CrossEntropyLoss
from libauc.optimizers import PESG, Adam
from libauc.datasets import CheXpert
from torchvision.models.resnet import resnet18
import pytorch_lightning as pl
from pytorch_lightning.loggers import WandbLogger
from sklearn.metrics import roc_auc_score

import torch 
from PIL import Image
import numpy as np
import torchvision.transforms as transforms
from torch.utils.data import Dataset
from sklearn.metrics import roc_auc_score
import torch.nn.functional as F
import torch.nn as nn
import numpy as np

DATA_PATH = "/scratch/va2134/datasets/CheXpert-v1.0-small/"
BATCH_SIZE = 32
SEED = 13
NUM_CLASS = 5
MAX_EPOCHS = 5
MODEL_SAVE_NAME = 'multi-label-contrastive-resnet18'
SAVE_PATH = '/scratch/va2134/models/finetuning/'
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

_= pl.seed_everything(SEED)
wandb_logger = WandbLogger(project="medical-cv")

train_set = CheXpert(csv_path = f'{DATA_PATH}/train.csv', 
                     image_root_path = DATA_PATH, 
                     use_upsampling = False, 
                     use_frontal = True, 
                     image_size=224, mode='train', 
                     class_index = -1, 
                     verbose = False
                    )
valid_set = CheXpert(csv_path = f'{DATA_PATH}/valid.csv', 
                     image_root_path = DATA_PATH, 
                     use_upsampling = False, 
                     use_frontal = True, 
                     image_size=224, 
                     mode='valid', 
                     class_index = -1, 
                     verbose = False
                    )
train_loader =  torch.utils.data.DataLoader(train_set, batch_size=BATCH_SIZE, num_workers=8, shuffle=True)
val_loader =  torch.utils.data.DataLoader(valid_set, batch_size=BATCH_SIZE, num_workers=8, shuffle=False)

class CheXpertModule(pl.LightningModule):
    def __init__(
        self,
        model,
        imratio
    ):
        super().__init__()
        self.imratio = imratio
        self.model = model
        self.model.fc = nn.Linear(512,NUM_CLASS)
        self.loss = AUCM_MultiLabel(imratio = self.imratio, num_classes = NUM_CLASS)
        self.learning_rate = 0.1 
        self.gamma = 500
        self.weight_decay = 1e-5
        self.margin = 1.0
        self.roc_auc_score = roc_auc_score
        
    def forward(self, x):
        return self.model(x)
    
    def shared_step(self, batch):
        x, y = batch
        logits = self(x)
        ȳ = torch.sigmoid(logits)
        return self.loss(y, ȳ), (y,ȳ)

    def training_step(self, batch, batch_idx):
        loss,_ = self.shared_step(batch)
        self.log("train_loss", loss.item(), on_step = True, on_epoch = False)
        return loss

    def validation_step(self, batch, batch_idx):
        loss,(y,ȳ) = self.shared_step(batch)
        self.log("val_loss", loss.item(), on_step = False, on_epoch = True)
        return loss,(y,ȳ)
    
    def validation_epoch_end(self,val_step_outputs):
      ground_truth = []
      predictions = []
      losses = []
      for (loss,(y,ȳ)) in val_step_outputs:
        losses.append(loss.item())
        ground_truth.append(y.cpu().numpy())
        predictions.append(ȳ.cpu().detach().numpy())
      ground_truth = np.concatenate(ground_truth)
      predictions = np.concatenate(predictions)
      val_loss = np.mean(np.asarray(losses))
      val_auc =  self.roc_auc_score(ground_truth, predictions) 
      self.log("val_roc_auc_score", val_auc, on_step = False, on_epoch = True)

    def configure_optimizers(self):
        optimizer = PESG(model, 
                        a = self.loss.a, 
                        b = self.loss.b, 
                        alpha = self.loss.alpha, 
                        lr = self.learning_rate, 
                        gamma = self.gamma, 
                        margin = self.margin, 
                        weight_decay = self.weight_decay, device = device
                      )

        return [optimizer]

model_save_checkpoint = pl.callbacks.ModelCheckpoint(
    monitor = 'val_loss',
    dirpath = SAVE_PATH,
    filename = f"{MODEL_SAVE_NAME}"+'-{epoch:02d}-{val_loss:.2f}',
    save_top_k = 1,
    mode = 'min',
)

model = CheXpertModule.load_from_checkpoint("/scratch/va2134/barlow-resnet18-epoch=10-val_loss=14.00.ckpt")


trainer = pl.Trainer(
    gpus = torch.cuda.device_count(),
    precision = 16 if torch.cuda.device_count() > 0 else 32,
    logger = wandb_logger
)

trainer.validate(model = model, dataloaders = val_loader)
