import time
import torch
import torch.utils.data
import torch.distributed as dist
from torch.utils.tensorboard import SummaryWriter
from torch.cuda import amp
import os
import datetime
from .. import functional

def use_dist():
    # whether use distributed training
    if not dist.is_available():
        return False
    if not dist.is_initialized():
        return False
    return True

def on_master():
    if use_dist():
        return dist.get_rank() == 0
    else:
        return True


def cal_correct(output, target, topk=(1,)):
    # modified by def accuracy() in https://github.com/pytorch/vision/blob/main/references/classification/utils.py
    with torch.no_grad():
        maxk = max(topk)

        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target[None])

        res = []
        for k in topk:
            correct_k = correct[:k].flatten().sum(dtype=torch.float32).item()
            res.append(correct_k)
        return res

def train_one_epoch(model, criterion, optimizer, data_loader, device, amp_scaler=None):
    model.train()
    train_acc1 = 0.
    train_acc5 = 0.
    train_loss = 0.
    samples_number = 0
    start_time = time.time()

    for image, target in data_loader:
        image, target = image.to(device), target.to(device)
        optimizer.zero_grad()

        if amp_scaler is not None:
            with amp.autocast():
                output = model(image)
                loss = criterion(output, target)
            amp_scaler.scale(loss).backward()
            amp_scaler.step(optimizer)
            amp_scaler.update()
        else:
            output = model(image)
            loss = criterion(output, target)
            loss.backward()
            optimizer.step()
        functional.reset_net(model)

        correct1, correct5 = cal_correct(output, target, topk=(1, 5))
        train_acc1 += correct1
        train_acc5 += correct5
        train_loss += loss.item() * image.shape[0]
        samples_number += image.shape[0]

    if use_dist():
        train_acc1 = torch.as_tensor(train_acc1, dtype=torch.float64, device=device)
        train_acc5 = torch.as_tensor(train_acc5, dtype=torch.float64, device=device)
        train_loss = torch.as_tensor(train_loss, dtype=torch.float64, device=device)
        samples_number = torch.as_tensor(samples_number, dtype=torch.float64, device=device)
        dist.barrier()

        dist.all_reduce(train_acc1)
        dist.all_reduce(train_acc5)
        dist.all_reduce(train_loss)
        dist.all_reduce(samples_number)

        train_acc1 = train_acc1.item()
        train_acc5 = train_acc5.item()
        train_loss = train_loss.item()
        samples_number = samples_number.item()

    train_acc1 /= samples_number
    train_acc5 /= samples_number
    train_acc1 *= 100.
    train_acc5 *= 100.
    train_loss /= samples_number


    print(f'Train: train_acc1={train_acc1:.3f}, train_acc5={train_acc5:.3f}, train_loss={train_loss:.6f}, samples/s={samples_number / (time.time() - start_time):.3f}')
    return train_acc1, train_acc5, train_loss

def evaluate(model, criterion, data_loader, device):
    model.eval()
    test_acc1 = 0.
    test_acc5 = 0.
    test_loss = 0.
    samples_number = 0
    start_time = time.time()

    for image, target in data_loader:
        image, target = image.to(device), target.to(device)

        output = model(image)
        loss = criterion(output, target)

        functional.reset_net(model)

        correct1, correct5 = cal_correct(output, target, topk=(1, 5))
        test_acc1 += correct1
        test_acc5 += correct5
        test_loss += loss.item() * image.shape[0]
        samples_number += image.shape[0]

        if use_dist():
            test_acc1 = torch.as_tensor(test_acc1, dtype=torch.float64, device=device)
            test_acc5 = torch.as_tensor(test_acc5, dtype=torch.float64, device=device)
            test_loss = torch.as_tensor(test_loss, dtype=torch.float64, device=device)
            samples_number = torch.as_tensor(samples_number, dtype=torch.float64, device=device)

            dist.barrier()

            dist.all_reduce(test_acc1)
            dist.all_reduce(test_acc5)
            dist.all_reduce(test_loss)
            dist.all_reduce(samples_number)

            test_acc1 = test_acc1.item()
            test_acc5 = test_acc5.item()
            test_loss = test_loss.item()
            samples_number = samples_number.item()

    test_acc1 /= samples_number
    test_acc5 /= samples_number
    test_acc1 *= 100.
    test_acc5 *= 100.
    test_loss /= samples_number

    print(f'Test: test_acc1={test_acc1:.3f}, test_acc5={test_acc5:.3f}, train_loss={test_loss:.6f}, samples/s={samples_number / (time.time() - start_time):.3f}')
    return test_acc1, test_acc5, test_loss
def train_eval_loop(args, device, model, criterion, optimizer, lr_scheduler, train_data_loader, test_data_loader, max_epoch, use_amp=False, tb_log_dir: str=None, pt_dir: str=None, resume_pt :str=None):

    start_epoch = 0
    if resume_pt is not None:
        checkpoint = torch.load(resume_pt, map_location='cpu')
        if isinstance(model, torch.nn.parallel.DistributedDataParallel):
            model.module.load_state_dict(checkpoint['model'])
        else:
            model.load_state_dict(checkpoint['model'])
        optimizer.load_state_dict(checkpoint['optimizer'])
        if lr_scheduler is not None:
            lr_scheduler.load_state_dict(checkpoint['lr_scheduler'])
        start_epoch = checkpoint['epoch'] + 1

    if use_amp:
        amp_scaler = amp.GradScaler()
    else:
        amp_scaler = None

    if on_master() and tb_log_dir is not None:
        tb_writer = SummaryWriter(tb_log_dir, purge_step=start_epoch)
    else:
        tb_writer = None

    if on_master() and pt_dir is not None and not os.path.exists(pt_dir):
        os.makedirs(pt_dir)
    max_test_acc1 = -1.
    test_acc5_at_max_test_acc1 = -1.
    max_test_acc5 = -1.


    for epoch in range(start_epoch, max_epoch):
        start_time = time.time()
        print(f'epoch={epoch}, args={args}')
        acc1, acc5, loss = train_one_epoch(model, criterion, optimizer, train_data_loader, device, amp_scaler)
        if tb_writer is not None:
            tb_writer.add_scalar('train_loss', loss, epoch)
            tb_writer.add_scalar('train_acc1', acc1, epoch)
            tb_writer.add_scalar('train_acc5', acc5, epoch)

        if lr_scheduler is not None:
            lr_scheduler.step()

        acc1, acc5, loss = evaluate(model, criterion, test_data_loader, device)

        if tb_writer is not None:
            tb_writer.add_scalar('test_loss', loss, epoch)
            tb_writer.add_scalar('test_acc1', acc1, epoch)
            tb_writer.add_scalar('test_acc5', acc5, epoch)

        if on_master() and pt_dir is not None:
            if isinstance(model, torch.nn.parallel.DistributedDataParallel):
                model_state_dict = model.module.state_dict()
            else:
                model_state_dict = model.state_dict()
            if lr_scheduler is not None:
                checkpoint = {
                    'model': model_state_dict,
                    'optimizer': optimizer.state_dict(),
                    'lr_scheduler': lr_scheduler.state_dict(),
                    'epoch': epoch,
                    'args': args,
                    'max_test_acc1': max_test_acc1,
                    'test_acc5_at_max_test_acc1': test_acc5_at_max_test_acc1,
                }
            else:
                checkpoint = {
                    'model': model_state_dict,
                    'optimizer': optimizer.state_dict(),
                    'epoch': epoch,
                    'args': args,
                    'max_test_acc1': max_test_acc1,
                    'test_acc5_at_max_test_acc1': test_acc5_at_max_test_acc1,
                }
            torch.save(checkpoint, os.path.join(pt_dir, 'ckp_latest.pt'))

        max_test_acc5 = max(acc5, max_test_acc5)
        if acc1 > max_test_acc1:
            max_test_acc1 = acc1
            test_acc5_at_max_test_acc1 = acc5
            if on_master() and pt_dir is not None:
                torch.save(checkpoint, os.path.join(pt_dir, 'ckp_max_test_acc1.pt'))
        used_time = time.time() - start_time
        print(f'Test: max_test_acc1={max_test_acc1:.3f}, max_test_acc5={max_test_acc5:.3f}, test_acc5_at_max_test_acc1={test_acc5_at_max_test_acc1:.3f}')
        if tb_log_dir is not None:
            print('tensorboard log dir=', tb_log_dir)
        if pt_dir is not None:
            print('pt dir=', pt_dir)
        print(f'escape time={(datetime.datetime.now() + datetime.timedelta(seconds=used_time * (max_epoch - epoch))).strftime("%Y-%m-%d %H:%M:%S")}\n')































